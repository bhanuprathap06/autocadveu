"""Background worker: claims jobs and runs each in an isolated process group.

Run: ``python -m autoforge.jobs.worker``

Per job: a fresh temporary directory, a child process (own process group) with
an address-space limit, wall-clock timeout, cancellation by killing the whole
group, heartbeat + progress streaming, and a handler-specific commit step in
the parent (artifact upload, result rows) inside one DB transaction.
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import shutil
import signal
import socket
import tempfile
import time
import traceback
import uuid
from typing import Any, Callable

from ..core import events, mail
from ..core.config import get_settings
from ..db import models as M
from ..db.session import session_scope
from . import queue

log = logging.getLogger("autoforge.worker")


# --------------------------------------------------------------------------- child side
def _child_entry(kind: str, payload: dict[str, Any], workdir: str, mem_mb: int) -> None:
    os.setpgrp()
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
    except Exception:
        pass
    prog_path = os.path.join(workdir, "progress.jsonl")

    def progress(stage: str, frac: float) -> None:
        with open(prog_path, "a") as fh:
            fh.write(json.dumps({"stage": stage, "fraction": frac, "t": time.time()}) + "\n")

    try:
        result = CHILD_HANDLERS[kind](payload, workdir, progress)
        with open(os.path.join(workdir, "result.json"), "w") as fh:
            json.dump({"ok": True, "result": result}, fh, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
        os._exit(0)
    except Exception as exc:
        code = getattr(exc, "code", "job_failed")
        with open(os.path.join(workdir, "result.json"), "w") as fh:
            json.dump({"ok": False, "code": code, "error": str(exc), "log": getattr(exc, "log", ""),
                       "trace": traceback.format_exc(limit=5)}, fh)
        os._exit(3)


def _simulation_child(payload: dict[str, Any], workdir: str, progress) -> dict[str, Any]:
    from ..kernel import occ
    from ..sim import pipeline

    occ.quiet()
    s = get_settings()
    from ..kernel.ops import _loader

    return pipeline.run(payload["content"], payload["setup"], workdir, ccx=s.ccx_binary,
                        timeout_s=int(payload.get("timeout_s", s.sim_max_runtime_s)), mem_mb=s.sim_memory_mb,
                        max_nodes=s.sim_max_mesh_nodes, blob_loader=_loader(payload.get("blob_prefix")), progress=progress)


def _sleep_child(payload: dict[str, Any], workdir: str, progress) -> dict[str, Any]:  # test/diagnostic handler
    progress("sleeping", 0.1)
    time.sleep(float(payload.get("seconds", 1)))
    if payload.get("crash"):
        os._exit(9)
    if payload.get("fail"):
        raise RuntimeError("requested failure")
    return {"slept": payload.get("seconds", 1)}


CHILD_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {"simulation": _simulation_child, "diagnostic_sleep": _sleep_child}


# --------------------------------------------------------------------------- parent side commit hooks
def _commit_simulation(db, job: M.Job, outcome: dict[str, Any], workdir: str) -> None:
    from sqlalchemy import select

    from ..core.storage import get_storage

    sim = db.scalar(select(M.SimulationJob).where(M.SimulationJob.job_id == job.id))
    if sim is None:
        return
    st = get_storage()
    arts: dict[str, Any] = {}

    def put(name: str, data: bytes, ctype: str) -> None:
        import hashlib

        key = f"projects/{sim.project_id}/simulations/{sim.id}/{name}"
        st.put(key, data, ctype)
        arts[name] = {"key": key, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "content_type": ctype}

    if outcome.get("ok"):
        res = outcome["result"]
        put("field.json", json.dumps(res.pop("viz")).encode(), "application/json")
        put("solver.log", res.pop("log", "").encode(), "text/plain")
        job_name = res.pop("job", "job_a")
        for ext, ct in ((".inp", "text/plain"), (".frd", "text/plain"), (".dat", "text/plain")):
            p = os.path.join(workdir, job_name + ext)
            if os.path.exists(p):
                with open(p, "rb") as fh:
                    put(f"model{ext}", fh.read(), ct)
        sim.solver_version = res["solver"]["version"]
        db.add(M.SimulationResult(simulation_id=sim.id, summary=res["summary"], mesh=res["mesh"],
                                  convergence=res.get("convergence"), warnings=res.get("warnings", []), artifacts=arts))
        job.result = {"summary": res["summary"], "solver": res["solver"], "inputs": res["inputs"],
                      "deck_info": res.get("deck_info"), "solver_run": res.get("solver_run")}
    elif outcome.get("log"):
        put("solver.log", outcome["log"].encode(), "text/plain")
        job.result = {"artifacts": arts}


COMMIT_HOOKS: dict[str, Callable[..., None]] = {"simulation": _commit_simulation}


class Worker:
    def __init__(self, worker_id: str | None = None, kinds: list[str] | None = None):
        self.id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.kinds = kinds
        self.running = True
        self.s = get_settings()

    def process_one(self) -> bool:
        with session_scope() as db:
            job = queue.claim(db, self.id, self.kinds)
            if job is None:
                return False
            jid, kind, payload, timeout_s, pid = job.id, job.kind, dict(job.payload), job.timeout_s, job.project_id
        self._publish(pid, jid, "running", {"stage": "starting", "fraction": 0.0})
        workdir = tempfile.mkdtemp(prefix=f"afjob-{jid}-")
        try:
            status, outcome = self._run_child(jid, kind, payload, timeout_s, workdir, pid)
            with session_scope() as db:
                job = db.get(M.Job, jid)
                hook = COMMIT_HOOKS.get(kind)
                if hook and outcome is not None:
                    hook(db, job, outcome, workdir)
                if status == "succeeded" and job.result is None:
                    job.result = outcome.get("result") if outcome else None
                err = None if status == "succeeded" else (outcome or {}).get("error") or status
                queue.finish(db, job, status, job.result, err if status != "succeeded" else None)
            self._publish(pid, jid, status, {"stage": status, "fraction": 1.0}, error=None if status == "succeeded" else err)
        except Exception as exc:  # never leave a job running because the commit failed
            log.exception("job %s commit failed", jid)
            with session_scope() as db:
                job = db.get(M.Job, jid)
                queue.finish(db, job, "failed", error=f"Internal error while storing results: {exc}")
            self._publish(pid, jid, "failed", None, error=str(exc))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return True

    def _run_child(self, jid, kind, payload, timeout_s, workdir, pid) -> tuple[str, dict[str, Any] | None]:
        if kind not in CHILD_HANDLERS:
            return "failed", {"ok": False, "error": f"Unknown job kind {kind!r}"}
        ctx = mp.get_context("spawn")
        proc = ctx.Process(target=_child_entry, args=(kind, payload, workdir, self.s.sim_memory_mb), daemon=False)
        proc.start()
        t0 = time.time()
        seen = 0
        last_prog = None
        while proc.is_alive():
            proc.join(1.0)
            prog_path = os.path.join(workdir, "progress.jsonl")
            if os.path.exists(prog_path):
                with open(prog_path) as fh:
                    lines = fh.readlines()
                if len(lines) > seen:
                    seen = len(lines)
                    last_prog = json.loads(lines[-1])
                    self._publish(pid, jid, "running", last_prog)
            with session_scope() as db:
                cancel, _ = queue.heartbeat(db, jid, last_prog)
            if cancel:
                self._kill(proc)
                return "cancelled", {"ok": False, "error": "Cancelled by user"}
            if time.time() - t0 > timeout_s:
                self._kill(proc)
                return "timed_out", {"ok": False, "error": f"Job exceeded its {timeout_s} s time limit and was stopped"}
        rp = os.path.join(workdir, "result.json")
        if not os.path.exists(rp):
            return "failed", {"ok": False, "error": f"Job process terminated unexpectedly (exit code {proc.exitcode}); "
                                                    "possible out-of-memory or crash"}
        with open(rp) as fh:
            outcome = json.load(fh)
        if outcome.get("ok"):
            return "succeeded", outcome
        code = outcome.get("code")
        return ("timed_out" if code == "timed_out" else "cancelled" if code == "cancelled" else "failed"), outcome

    @staticmethod
    def _kill(proc) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.join(10)

    @staticmethod
    def _publish(pid, jid, status, progress, error=None) -> None:
        if pid:
            events.publish(f"project:{pid}", {"type": "job.status", "job_id": str(jid), "status": status,
                                              "progress": progress, "error": error})

    def housekeeping(self) -> None:
        with session_scope() as db:
            queue.recover_stale(db, stale_after_s=max(60, 10 * self.s.job_poll_interval_s))
            mail.deliver_pending(db)

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        log.info("worker %s started", self.id)
        last_hk = 0.0
        while self.running:
            if time.time() - last_hk > 15:
                try:
                    self.housekeeping()
                except Exception:
                    log.exception("housekeeping failed")
                last_hk = time.time()
            try:
                busy = self.process_one()
            except Exception:
                log.exception("job processing failed")
                busy = False
            if not busy:
                time.sleep(self.s.job_poll_interval_s)
        log.info("worker %s stopped", self.id)


def main() -> None:
    from ..api.main import configure_logging

    configure_logging()
    Worker().run_forever()


if __name__ == "__main__":
    main()
