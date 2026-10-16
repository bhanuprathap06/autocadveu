"""Write the OpenAPI schema used to generate the typed web client (apps/web/src/api/schema.d.ts)."""

import json
import os
import sys

os.environ.setdefault("AF_ENV", "test")
os.environ.setdefault("AF_KERNEL_WORKERS", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoforge.api.main import create_app  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"
with open(out, "w") as fh:
    json.dump(create_app().openapi(), fh, indent=1, sort_keys=True)
print(f"wrote {out}")
