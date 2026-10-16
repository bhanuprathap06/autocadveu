"""CAD file import/export.

Support tiers (see docs/file-formats.md):

=========  ======  ======  =====================================================
Format     Import  Export  Retains
=========  ======  ======  =====================================================
Native     yes     yes     Full parametric history, parameters, sketches,
                           constraints, materials, annotations (JSON + manifest)
STEP       yes     yes     Exact B-rep geometry, body/product names, assembly
                           structure + placements on export. NO feature history.
IGES       yes     yes     B-rep geometry (BRep mode). No names, no history.
BREP       yes     yes     OCCT native B-rep. Geometry only.
STL        no      yes     Triangle mesh only (binary). Lossy, no topology.
OBJ        no      yes     Triangle mesh + normals. Lossy.
GLB        no      yes     glTF 2.0 binary mesh for visualisation. Lossy.
=========  ======  ======  =====================================================
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
from typing import Any

import numpy as np
from OCP.BRep import BRep_Builder
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_RetDone
from OCP.IGESControl import IGESControl_Reader, IGESControl_Writer
from OCP.Interface import Interface_Static
from OCP.ShapeFix import ShapeFix_Shape
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader
from OCP.StlAPI import StlAPI_Writer
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopoDS import TopoDS_Shape
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool

from . import occ
from .naming import FeatureError
from .tessellate import mesh_arrays, mesh_shape

MAX_IMPORT_BYTES = 200 * 1024 * 1024


class ImportError_(FeatureError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_format(data: bytes, filename: str = "") -> str | None:
    head = data[:2048].lstrip(b"\xef\xbb\xbf \r\n\t")
    if head.startswith(b"ISO-10303-21"):
        return "step"
    if head.startswith(b"DBRep_DrawableShape") or head.startswith(b"CASCADE Topology"):
        return "brep"
    first = head.split(b"\n", 1)[0].rstrip(b"\r")
    if len(first) >= 73 and first[72:73] == b"S":
        return "iges"
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return None if not ext else {"stl": "stl", "obj": "obj", "glb": "glb"}.get(ext)


def _tmp(data: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


def read_shape_bytes(data: bytes, fmt: str | None = None, filename: str = "") -> tuple[Any, dict[str, Any]]:
    """Parse, heal and validate a CAD file. Returns (shape, diagnostics)."""
    if len(data) == 0:
        raise ImportError_("File is empty", "invalid_file")
    if len(data) > MAX_IMPORT_BYTES:
        raise ImportError_(f"File exceeds {MAX_IMPORT_BYTES // (1024 * 1024)} MB import limit", "file_too_large")
    detected = sniff_format(data, filename)
    if fmt and detected and detected != fmt:
        raise ImportError_(f"File content looks like {detected.upper()}, not {fmt.upper()}", "format_mismatch")
    fmt = fmt or detected
    if fmt not in ("step", "iges", "brep"):
        raise ImportError_("Unsupported or unrecognised CAD format. Supported for import: STEP, IGES, BREP. "
                           "Mesh formats (STL/OBJ) cannot be converted to editable solids.", "unsupported_format")
    diag: dict[str, Any] = {"format": fmt, "bytes": len(data), "sha256": sha256(data), "warnings": []}
    path = _tmp(data, "." + fmt)
    try:
        if fmt == "step":
            occ.st(Interface_Static, "SetCVal")("xstep.cascade.unit", "MM")
            r = STEPControl_Reader()
            status = r.ReadFile(path)
            if status != IFSelect_RetDone:
                raise ImportError_("STEP file could not be parsed (syntax error or unsupported schema)", "parse_failed")
            diag["roots"] = int(r.NbRootsForTransfer())
            n = r.TransferRoots()
            if n == 0:
                raise ImportError_("STEP file contains no transferable geometry", "no_geometry")
            shape = r.OneShape()
        elif fmt == "iges":
            r = IGESControl_Reader()
            if r.ReadFile(path) != IFSelect_RetDone:
                raise ImportError_("IGES file could not be parsed", "parse_failed")
            r.TransferRoots()
            shape = r.OneShape()
        else:
            shape = TopoDS_Shape()
            ok = occ.st(BRepTools, "Read")(shape, path, BRep_Builder())
            if not ok:
                raise ImportError_("BREP file could not be parsed", "parse_failed")
    finally:
        os.unlink(path)
    if shape is None or shape.IsNull():
        raise ImportError_("File produced an empty shape", "no_geometry")
    valid_before = occ.is_valid(shape)
    if not valid_before:
        fix = ShapeFix_Shape(shape)
        fix.Perform()
        shape = fix.Shape()
        diag["warnings"].append("Geometry was invalid on import and was healed with ShapeFix")
    diag["valid"] = occ.is_valid(shape)
    diag["valid_before_healing"] = valid_before
    diag["solids"] = len(occ.solids(shape))
    diag["faces"] = len(occ.faces(shape))
    diag["edges"] = len(occ.edges(shape))
    bb = occ.bbox(shape)
    diag["bbox_mm"] = bb
    if bb:
        size = float(np.max(np.subtract(bb[1], bb[0])))
        diag["max_extent_mm"] = size
        if size < 0.1:
            diag["warnings"].append("Model is smaller than 0.1 mm: check the source units")
        if size > 1e5:
            diag["warnings"].append("Model is larger than 100 m: check the source units")
    if not diag["valid"]:
        diag["warnings"].append("Geometry is still invalid after healing; downstream operations may fail")
    if diag["solids"]:
        diag["volume_mm3"] = occ.volume_props(shape).Mass()
    return shape, diag


# ------------------------------------------------------------------ exports
def export_step(items: list[tuple[str, Any]], product_name: str = "AutoForge",
                placements: list[tuple[str, int, Any]] | None = None) -> bytes:
    """STEP AP214 via XDE so body/product names (and assembly structure) survive.

    ``items``: [(name, shape)] parts. ``placements``: optional assembly instances
    [(instance_name, item_index, gp_Trsf)]; when given an assembly is written.
    """
    from OCP.TopLoc import TopLoc_Location

    app = XCAFApp_Application.GetApplication_s() if hasattr(XCAFApp_Application, "GetApplication_s") else XCAFApp_Application.GetApplication()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    app.InitDocument(doc)
    st = occ.st(XCAFDoc_DocumentTool, "ShapeTool")(doc.Main())
    labels = []
    for name, shape in items:
        lab = st.AddShape(shape, False)
        occ.st(TDataStd_Name, "Set")(lab, TCollection_ExtendedString(name))
        labels.append(lab)
    if placements:
        assy = st.NewShape()
        occ.st(TDataStd_Name, "Set")(assy, TCollection_ExtendedString(product_name))
        for inst_name, idx, trsf in placements:
            comp = st.AddComponent(assy, labels[idx], TopLoc_Location(trsf))
            occ.st(TDataStd_Name, "Set")(comp, TCollection_ExtendedString(inst_name))
        st.UpdateAssemblies()
    occ.st(Interface_Static, "SetCVal")("write.step.unit", "MM")
    occ.st(Interface_Static, "SetCVal")("write.step.product.name", product_name)
    w = STEPCAFControl_Writer()
    w.SetNameMode(True)
    if not w.Transfer(doc, STEPControl_AsIs):
        raise FeatureError("STEP transfer failed", "export_failed")
    fd, path = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        if w.Write(path) != IFSelect_RetDone:
            raise FeatureError("STEP write failed", "export_failed")
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


def export_iges(shapes: list[Any]) -> bytes:
    w = IGESControl_Writer("MM", 1)
    for s in shapes:
        w.AddShape(s)
    w.ComputeModel()
    fd, path = tempfile.mkstemp(suffix=".igs")
    os.close(fd)
    try:
        if not w.Write(path):
            raise FeatureError("IGES write failed", "export_failed")
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


def export_brep(shape: Any) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    try:
        occ.st(BRepTools, "Write")(shape, path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


def export_stl(shape: Any, quality: str = "fine") -> bytes:
    mesh_shape(shape, quality)
    fd, path = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    try:
        w = StlAPI_Writer()
        w.ASCIIMode = False
        if hasattr(w, "SetASCIIMode"):
            w.SetASCIIMode(False)
        if not w.Write(shape, path):
            raise FeatureError("STL write failed", "export_failed")
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


def export_obj(items: list[tuple[str, Any]], quality: str = "normal") -> bytes:
    lines = ["# AutoForge Engineering Studio OBJ export (mesh only, units: mm)"]
    off = 0
    for name, shape in items:
        P, N, I = mesh_arrays(shape, quality)
        lines.append(f"o {name}")
        lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in P]
        lines += [f"vn {x:.6f} {y:.6f} {z:.6f}" for x, y, z in N]
        lines += [f"f {a + off + 1}//{a + off + 1} {b + off + 1}//{b + off + 1} {c + off + 1}//{c + off + 1}" for a, b, c in I]
        off += len(P)
    return ("\n".join(lines) + "\n").encode()


def export_glb(items: list[tuple[str, Any, tuple[float, float, float]]], quality: str = "normal") -> bytes:
    """Minimal glTF 2.0 binary: one mesh per body, metres as per glTF spec."""
    buf = bytearray()
    views, accessors, meshes, nodes, materials = [], [], [], [], []

    def add_view(data: bytes, target: int) -> int:
        while len(buf) % 4:
            buf.append(0)
        views.append({"buffer": 0, "byteOffset": len(buf), "byteLength": len(data), "target": target})
        buf.extend(data)
        return len(views) - 1

    for name, shape, color in items:
        P, N, I = mesh_arrays(shape, quality)
        P = (P / 1000.0).astype(np.float32)  # glTF uses metres
        N = N.astype(np.float32)
        I = I.astype(np.uint32)
        pv = add_view(P.tobytes(), 34962)
        nv = add_view(N.tobytes(), 34962)
        iv = add_view(I.tobytes(), 34963)
        accessors.append({"bufferView": pv, "componentType": 5126, "count": len(P), "type": "VEC3",
                          "min": P.min(0).tolist() if len(P) else [0, 0, 0], "max": P.max(0).tolist() if len(P) else [0, 0, 0]})
        pa = len(accessors) - 1
        accessors.append({"bufferView": nv, "componentType": 5126, "count": len(N), "type": "VEC3"})
        na = len(accessors) - 1
        accessors.append({"bufferView": iv, "componentType": 5125, "count": int(I.size), "type": "SCALAR"})
        ia = len(accessors) - 1
        materials.append({"pbrMetallicRoughness": {"baseColorFactor": [*color, 1.0], "metallicFactor": 0.6,
                                                   "roughnessFactor": 0.45}, "name": name})
        meshes.append({"name": name, "primitives": [{"attributes": {"POSITION": pa, "NORMAL": na}, "indices": ia,
                                                     "material": len(materials) - 1}]})
        nodes.append({"name": name, "mesh": len(meshes) - 1})
    while len(buf) % 4:
        buf.append(0)
    gltf = {
        "asset": {"version": "2.0", "generator": "AutoForge Engineering Studio"},
        "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes, "meshes": meshes, "materials": materials,
        "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(buf)}],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(buf)
    out = struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<I4s", len(js), b"JSON") + js
    out += struct.pack("<I4s", len(buf), b"BIN\x00") + bytes(buf)
    return out


def parse_stl_binary(data: bytes) -> tuple[int, np.ndarray]:
    """Used by export verification tests: returns (triangle count, vertices)."""
    n = struct.unpack("<I", data[80:84])[0]
    if len(data) != 84 + 50 * n:
        raise ValueError("Binary STL length mismatch")
    rec = np.frombuffer(data[84:], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=n)
    return n, rec["v"].reshape(-1, 3)
