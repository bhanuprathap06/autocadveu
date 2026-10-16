"""Isolation guarantees of the kernel worker pool (real processes)."""

import pytest

from autoforge.kernel_pool import KernelCrashed, KernelError, KernelPool, KernelTimeout
from autoforge.catalog.templates import build_template


@pytest.fixture(scope="module")
def pool():
    p = KernelPool(1, mem_mb=4096, timeout=30, allow_test_ops=True)
    yield p
    p.shutdown()


def test_ping_and_recompute(pool):
    assert pool.call("ping")["kernel"].startswith("OCCT")
    out = pool.call("recompute", doc=build_template("l_bracket").model_dump(mode="json"))
    assert out["ok"] and out["bodies"][0]["triangle_count"] > 0


def test_timeout_kills_and_recovers(pool):
    pid = pool.status()[0]["pid"]
    with pytest.raises(KernelTimeout):
        pool.call("_test_sleep", timeout=1, seconds=10)
    st = pool.status()[0]
    assert st["pid"] != pid and st["alive"] and st["restarts"] >= 1
    assert pool.call("ping")["pid"] == st["pid"]


def test_crash_is_contained(pool):
    with pytest.raises(KernelCrashed):
        pool.call("_test_crash")
    assert pool.call("ping")["kernel"]


def test_structured_feature_errors(pool):
    with pytest.raises(KernelError) as e:
        pool.call("export", doc={"features": []}, fmt="step")
    assert e.value.code == "nothing_to_export"
    with pytest.raises(KernelError) as e2:
        pool.call("inspect_import", data=b"ISO-10303-21;\nX", fmt="step", filename="x.step")
    assert e2.value.code in ("parse_failed", "no_geometry")
