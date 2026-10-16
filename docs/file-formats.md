# File formats

| Format | Import | Export | Fidelity and verification |
|---|---|---|---|
| AutoForge native (`.afdoc.json`) | Yes | Yes | Complete parametric document (history, parameters, sketches with constraints, materials) plus manifest with schema/kernel versions and SHA-256; import rejects checksum mismatches. Imported blobs are referenced, not embedded. |
| STEP AP214 (`.step`, `.stp`) | Yes | Yes | Exact B-rep. Export via OCCT XDE keeps body/product names and assembly placements; units mm. Every export is re-imported and its volume compared with the model (relative error must be < 1e-4) before download; the result is returned in `X-Export-Verification`. Import heals invalid geometry with ShapeFix and reports diagnostics (solids, faces, validity before/after healing, volume). |
| IGES (`.igs`, `.iges`) | Yes | Yes | Exact surfaces; no product structure. Same re-import verification on export. |
| OCCT BREP (`.brep`) | Yes | Yes (one body) | Native OCCT exact format. |
| STL (binary) | No (mesh, not editable) | Yes | Tessellation of the exact B-rep; triangle count verified by parsing the written file. |
| OBJ | No | Yes | Tessellated mesh with one object per body. |
| glTF binary (`.glb`) | No | Yes | Tessellated meshes in metres (glTF convention) with PBR materials, for viewers. |
| PDF / DXF (R2010) / SVG | — | Drawings | Generated from the same layout primitives; DXF uses mm units and separate layers (border, visible, smooth, hidden, center, dim, text, annot, table). Export is blocked while GD&T validation reports errors. |
| CSV / Markdown / PDF | — | BOM, comparison and design-review reports | Masses state their basis; missing data is shown as missing. |

Limits: uploads up to `AF_MAX_UPLOAD_MB` (default 100 MB); the importer refuses files over 200 MB. Mesh formats
(STL/OBJ/GLB) can be stored as reference files but are not converted into solids, because doing so would fabricate
B-rep geometry. Parasolid, ACIS, native SolidWorks/CATIA/NX/Inventor formats and DXF import are not supported.
