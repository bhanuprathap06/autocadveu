import os
import shutil
import tempfile
import uuid

import pytest

_tmp = tempfile.mkdtemp(prefix="af-test-")
os.environ.setdefault("AF_ENV", "test")
os.environ.setdefault("AF_DATABASE_URL", "postgresql+psycopg://autoforge:autoforge@localhost:5432/autoforge_test")
os.environ["AF_STORAGE_BACKEND"] = "local"
os.environ["AF_STORAGE_LOCAL_DIR"] = os.path.join(_tmp, "objects")
os.environ.setdefault("AF_KERNEL_WORKERS", "0")
os.environ.setdefault("AF_ALLOWED_ORIGINS", '["http://localhost:3000"]')


def _db_available() -> bool:
    try:
        import psycopg

        url = os.environ["AF_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(url, connect_timeout=2):
            return True
    except Exception:
        return False


DB_OK = _db_available()


@pytest.fixture(scope="session")
def migrated():
    if not DB_OK:
        pytest.skip("PostgreSQL test database not available")
    from alembic import command
    from alembic.config import Config

    here = os.path.dirname(os.path.dirname(__file__))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    shutil.rmtree(_tmp, ignore_errors=True)


@pytest.fixture()
def db_clean(migrated):
    from sqlalchemy import text

    from autoforge.core import ratelimit
    from autoforge.db.session import get_engine

    with get_engine().begin() as c:
        tables = [r[0] for r in c.execute(text("select tablename from pg_tables where schemaname='public' and tablename <> 'alembic_version'"))]
        c.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))
    ratelimit.reset()
    yield


class Client:
    """TestClient wrapper that sends the CSRF header like the web app does."""

    def __init__(self, app):
        from fastapi.testclient import TestClient

        self.c = TestClient(app, base_url="http://testserver")

    def _h(self, headers=None):
        h = dict(headers or {})
        tok = self.c.cookies.get("af_csrf")
        if tok:
            h["x-csrf-token"] = tok
        return h

    def get(self, url, **kw):
        return self.c.get("/api/v1" + url, **kw)

    def post(self, url, json=None, headers=None, **kw):
        return self.c.post("/api/v1" + url, json=json, headers=self._h(headers), **kw)

    def put(self, url, json=None, **kw):
        return self.c.put("/api/v1" + url, json=json, headers=self._h(), **kw)

    def patch(self, url, json=None, **kw):
        return self.c.patch("/api/v1" + url, json=json, headers=self._h(), **kw)

    def delete(self, url, **kw):
        return self.c.delete("/api/v1" + url, headers=self._h(), **kw)

    def register(self, email=None, password="correct-horse-battery", name="Test Engineer"):
        email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
        r = self.post("/auth/register", {"email": email, "password": password, "display_name": name})
        assert r.status_code == 201, r.text
        self.email = email
        return r.json()["user"]


@pytest.fixture()
def app(db_clean):
    from autoforge.api.main import create_app

    return create_app()


@pytest.fixture()
def client(app):
    return Client(app)


@pytest.fixture()
def make_client(app):
    return lambda: Client(app)
