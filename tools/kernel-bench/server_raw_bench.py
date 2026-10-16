"""Raw OCCT operations on the server (OCP), mirroring browser-bench.html one-to-one.

Run from services/api with the project virtualenv:
    python ../../tools/kernel-bench/server_raw_bench.py
"""

import json
import math
import os
import resource
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "api"))

from OCP.BRep import BRep_Builder, BRep_Tool  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Curve  # noqa: E402
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut  # noqa: E402
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet  # noqa: E402
from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.BRepMesh import BRepMesh_IncrementalMesh  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.GeomAbs import GeomAbs_Line  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt  # noqa: E402
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopLoc import TopLoc_Location  # noqa: E402
from OCP.TopoDS import TopoDS, TopoDS_Compound  # noqa: E402

from autoforge.kernel import occ  # noqa: E402


def cyl(x, y, z0, r, h):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), r, h).Shape()


def box(x, y, z, dx, dy, dz):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()


def vol(s):
    g = GProp_GProps()
    occ.st(BRepGProp, "VolumeProperties")(s, g)
    return g.Mass()


def mesh(s, defl):
    BRepMesh_IncrementalMesh(s, defl, False, 0.5, False)
    n, ex = 0, TopExp_Explorer(s, TopAbs_FACE)
    while ex.More():
        tri = occ.st(BRep_Tool, "Triangulation")(TopoDS.Face(ex.Current()), TopLoc_Location())
        if tri is not None:
            n += tri.NbTriangles()
        ex.Next()
    return n


def stage(res, name, fn):
    t = time.perf_counter()
    out = fn() or {}
    res[name] = {"ms": round((time.perf_counter() - t) * 1000, 1), **out}


def main():
    occ.quiet()
    res = {"kernel": occ.kernel_version()}
    state = {}

    def small():
        s = box(0, 0, 0, 120, 60, 8)
        for x, y in [(10, 10), (110, 10), (110, 50), (10, 50)]:
            s = BRepAlgoAPI_Cut(s, cyl(x, y, -1, 3.3, 10)).Shape()
        fil, ex, k = BRepFilletAPI_MakeFillet(s), TopExp_Explorer(s, TopAbs_EDGE), 0
        while ex.More() and k < 4:
            e = TopoDS.Edge(ex.Current())
            if BRepAdaptor_Curve(e).GetType() == GeomAbs_Line:
                fil.Add(2, e)
                k += 1
            ex.Next()
        s = fil.Shape()
        t = time.perf_counter()
        n = mesh(s, 0.1)
        return {"volume_mm3": round(vol(s), 2), "tessellate_ms": round((time.perf_counter() - t) * 1000, 1), "triangles": n}

    def pattern():
        s = cyl(0, 0, 0, 150, 20)
        b, c = BRep_Builder(), TopoDS_Compound()
        b.MakeCompound(c)
        for i in range(60):
            a = 2 * math.pi * i / 60
            b.Add(c, cyl(120 * math.cos(a), 120 * math.sin(a), -1, 4, 22))
        s = BRepAlgoAPI_Cut(s, c).Shape()
        t = time.perf_counter()
        n = mesh(s, 0.1)
        state["disc"] = s
        return {"volume_mm3": round(vol(s), 2), "tessellate_ms": round((time.perf_counter() - t) * 1000, 1), "triangles": n}

    def step():
        w = STEPControl_Writer()
        w.Transfer(state["disc"], STEPControl_AsIs)
        path = os.path.join(tempfile.mkdtemp(), "disc.step")
        w.Write(path)
        return {"bytes": os.path.getsize(path)}

    def interference():
        boxes = [box(i * 15, (i % 3) * 5, 0, 20, 20, 20) for i in range(20)]
        hits = pairs = 0
        for i in range(20):
            for j in range(i + 1, 20):
                pairs += 1
                hits += vol(BRepAlgoAPI_Common(boxes[i], boxes[j]).Shape()) > 1e-6
        return {"pairs": pairs, "interfering": hits}

    stage(res, "small_part", small)
    stage(res, "pattern_part_60_holes", pattern)
    stage(res, "step_export", step)
    stage(res, "assembly_medium_interference", interference)
    res["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
