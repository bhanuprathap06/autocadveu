"""Post-restore (or periodic) integrity verification.

Checks, and exits non-zero on any failure:
  1. every revision's stored content hashes to its recorded content_sha256;
  2. every stored file, import-feature blob and simulation artifact exists in object storage with the recorded SHA-256;
  3. the head revision of up to N part documents regenerates in the CAD kernel without new feature errors
     compared with the recompute summary stored when the revision was saved.

    python scripts/verify_integrity.py [--recompute N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from autoforge.core.storage import get_storage  # noqa: E402
from autoforge.db import models as M  # noqa: E402
from autoforge.db.session import session_scope  # noqa: E402


def canonical_sha(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recompute", type=int, default=20)
    args = ap.parse_args()
    st = get_storage()
    problems: list[str] = []
    report: dict[str, int] = {"revisions": 0, "files": 0, "import_blobs": 0, "sim_artifacts": 0, "recomputed": 0}
    with session_scope() as db:
        for r in db.scalars(select(M.CADRevision)):
            report["revisions"] += 1
            if canonical_sha(r.content) != r.content_sha256:
                problems.append(f"revision {r.document_id}#{r.number}: content hash mismatch")
            for f in r.content.get("features", []) if isinstance(r.content, dict) else []:
                if f.get("type") == "import":
                    report["import_blobs"] += 1
                    try:
                        st.get(f["params"]["blob_key"], expected_sha256=f["params"]["sha256"])
                    except Exception as exc:
                        problems.append(f"revision {r.document_id}#{r.number}: import blob {f['params']['blob_key']}: {exc}")
        for f in db.scalars(select(M.StoredFile)):
            report["files"] += 1
            try:
                st.get(f.key, expected_sha256=f.sha256)
            except Exception as exc:
                problems.append(f"file {f.id} ({f.filename}): {exc}")
        for res in db.scalars(select(M.SimulationResult)):
            for name, a in (res.artifacts or {}).items():
                report["sim_artifacts"] += 1
                try:
                    st.get(a["key"], expected_sha256=a["sha256"])
                except Exception as exc:
                    problems.append(f"simulation {res.simulation_id} artifact {name}: {exc}")
        from autoforge.kernel_pool import get_pool

        docs = db.scalars(select(M.CADDocument).where(M.CADDocument.kind == "part", M.CADDocument.deleted_at.is_(None))
                          .limit(args.recompute)).all()
        for d in docs:
            rev = db.scalar(select(M.CADRevision).where(M.CADRevision.document_id == d.id, M.CADRevision.number == d.head_revision))
            if rev is None:
                problems.append(f"document {d.id}: head revision {d.head_revision} missing")
                continue
            res = get_pool().call("recompute", doc=rev.content, tessellate=False, blob_prefix=f"projects/{d.project_id}/")
            before = {f["id"] for f in (rev.recompute or {}).get("features", []) if f["status"] == "error"}
            after = {f["id"] for f in res["features"] if f["status"] == "error"}
            if after - before:
                problems.append(f"document {d.id} r{rev.number}: new feature errors after restore: {sorted(after - before)}")
            report["recomputed"] += 1
    print(json.dumps({"ok": not problems, **report, "problems": problems[:50]}, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
