"""Back up / restore the object store (S3-compatible or local) to a single tar archive with a checksum manifest.

    python scripts/objects_backup.py export /backup/objects.tar   (or "-" for stdout)
    python scripts/objects_backup.py import /backup/objects.tar   (or "-" for stdin)

Every object is stored with its SHA-256; import re-verifies each checksum before writing, so a corrupted archive is
rejected instead of silently restored.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoforge.core.config import get_settings  # noqa: E402
from autoforge.core.storage import get_storage  # noqa: E402


def _keys() -> list[str]:
    s = get_settings()
    st = get_storage()
    if s.storage_backend == "s3":
        keys: list[str] = []
        for page in st.client.get_paginator("list_objects_v2").paginate(Bucket=st.bucket):  # type: ignore[attr-defined]
            keys += [o["Key"] for o in page.get("Contents", [])]
        return keys
    root = os.path.abspath(s.storage_local_dir)
    return [os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/") for d, _, fs in os.walk(root) for f in fs]


def export(path: str) -> None:
    st = get_storage()
    manifest = []
    tar_kwargs = {"fileobj": sys.stdout.buffer, "mode": "w|"} if path == "-" else {"name": path, "mode": "w"}
    with tarfile.open(**tar_kwargs) as tar:
        for key in sorted(_keys()):
            data = st.get(key)
            sha = hashlib.sha256(data).hexdigest()
            info = tarfile.TarInfo(f"objects/{key}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            manifest.append({"key": key, "sha256": sha, "size": len(data)})
        raw = json.dumps({"format": "autoforge-objects-v1", "objects": manifest}, indent=1).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    print(json.dumps({"exported": len(manifest), "bytes": sum(m["size"] for m in manifest)}), file=sys.stderr)


def import_(path: str) -> None:
    st = get_storage()
    if path == "-":
        path_obj = io.BytesIO(sys.stdin.buffer.read())  # random access is needed to read the trailing manifest first
        tar_ctx = tarfile.open(fileobj=path_obj, mode="r")
    else:
        tar_ctx = tarfile.open(path, "r")
    with tar_ctx as tar:
        manifest = json.load(tar.extractfile("manifest.json"))  # type: ignore[arg-type]
        if manifest.get("format") != "autoforge-objects-v1":
            sys.exit("Not an AutoForge object archive")
        n = 0
        for m in manifest["objects"]:
            member = tar.extractfile(f"objects/{m['key']}")
            if member is None:
                sys.exit(f"Archive is missing {m['key']}")
            data = member.read()
            if hashlib.sha256(data).hexdigest() != m["sha256"]:
                sys.exit(f"Checksum mismatch for {m['key']}: archive is corrupted")
            st.put(m["key"], data)
            n += 1
    print(json.dumps({"imported": n}))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("export", "import"):
        sys.exit(__doc__)
    (export if sys.argv[1] == "export" else import_)(sys.argv[2])
