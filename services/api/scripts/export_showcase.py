"""Export starter-template models as glTF for the public landing page.

The landing page shows real kernel output, not hand-made art: each template is
rebuilt from its parametric definition, recomputed by OCCT, tessellated and
written as GLB together with a manifest recording the kernel version, volume
and SHA-256 of every file. Run after kernel or template changes:

    python scripts/export_showcase.py ../../apps/web/public/models
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoforge.catalog.templates import TEMPLATES, build_template  # noqa: E402
from autoforge.kernel import engine, io as kio, occ  # noqa: E402

SHOWCASE = ["brake_rotor", "control_arm_plate", "flange_coupling", "battery_enclosure"]


def main(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    occ.quiet()
    manifest = {"kernel": occ.kernel_version(), "models": []}
    for key in SHOWCASE:
        doc = build_template(key)
        res = engine.recompute(doc)
        if not res.ok:
            raise SystemExit(f"template {key} failed to recompute: {[f.to_dict() for f in res.features if f.status == 'error']}")
        bodies = list(res.ctx.bodies.items())
        data = kio.export_glb([(n, b.shape, (0.62, 0.66, 0.72)) for n, b in bodies], quality="fine")
        path = os.path.join(out_dir, f"{key}.glb")
        with open(path, "wb") as fh:
            fh.write(data)
        vol = sum(occ.volume_props(b.shape).Mass() for _, b in bodies)
        manifest["models"].append({"key": key, "name": TEMPLATES[key]["name"], "file": f"{key}.glb", "bytes": len(data),
                                   "sha256": hashlib.sha256(data).hexdigest(), "volume_mm3": round(vol, 3),
                                   "features": len(doc.features), "parameters": len(doc.parameters)})
        print(f"{key}: {len(data)} bytes, volume {vol:.1f} mm3")
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "showcase")
