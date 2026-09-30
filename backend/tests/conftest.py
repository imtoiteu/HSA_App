import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="hsa_app_tests_"))
os.environ.setdefault("HSA_TEST_DATABASE_URL", "postgresql+psycopg://hsa:test@127.0.0.1:54340/hsa_test")
os.environ["HSA_DATABASE_URL"] = os.environ["HSA_TEST_DATABASE_URL"]
os.environ["HSA_ENV"] = "test"
os.environ["HSA_MEDIA_ROOT"] = str(_TMP / "media")
os.environ["HSA_UPSTREAM_ROOT"] = str(_TMP / "upstream")
os.environ["HSA_SEPAY_API_KEY"] = "sepay-test-key"
os.environ["HSA_CASSO_SECURE_TOKEN"] = "casso-test-token"
os.environ["HSA_GENERIC_WEBHOOK_SECRET"] = "generic-test-secret"
os.environ["HSA_PUBLIC_BASE_URL"] = "http://testserver"
os.environ["HSA_RATE_LIMITS"] = "false"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, engine, reset_engine  # noqa: E402

from .fixtures.upstream_builder import build_standard  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def database():
    reset_engine()
    with engine().begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")
    from app.seed import seed
    db = SessionLocal()
    seed(db)
    db.close()
    yield
    reset_engine()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def upstream_ids():
    return build_standard(Path(os.environ["HSA_UPSTREAM_ROOT"]))


@pytest.fixture(scope="session")
def synced(upstream_ids):
    from app.sync.importer import sync_hsa
    db = SessionLocal()
    run = sync_hsa(db, Path(os.environ["HSA_UPSTREAM_ROOT"]), Path(os.environ["HSA_MEDIA_ROOT"]))
    db.close()
    return {"ids": upstream_ids, "run_stats": run.stats}


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


class Api:
    """TestClient wrapper that keeps cookies and sends the CSRF header like the web client."""

    def __init__(self, client):
        self.c = client
        self.csrf = None

    def _h(self):
        return {"X-CSRF-Token": self.csrf} if self.csrf else {}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json, headers={**self._h(), **kw.pop("headers", {})}, **kw)

    def put(self, url, json=None, **kw):
        return self.c.put(url, json=json, headers={**self._h(), **kw.pop("headers", {})}, **kw)

    def patch(self, url, json=None, **kw):
        return self.c.patch(url, json=json, headers={**self._h(), **kw.pop("headers", {})}, **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers={**self._h(), **kw.pop("headers", {})}, **kw)


def _client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


@pytest.fixture()
def anon():
    return Api(_client())


@pytest.fixture()
def student(synced):
    api = Api(_client())
    email = f"hs_{uuid.uuid4().hex[:8]}@example.com"
    r = api.post("/api/auth/register", {"email": email, "password": "matkhau-an-toan-1", "display_name": "Học sinh"})
    assert r.status_code == 200, r.text
    api.csrf = r.json()["user"]["csrf_token"]
    api.user = r.json()["user"]
    return api


@pytest.fixture()
def admin(synced):
    from app.auth import hash_password
    from app.models import User
    db = SessionLocal()
    email = f"admin_{uuid.uuid4().hex[:8]}@example.com"
    db.add(User(email=email, display_name="Admin", password_hash=hash_password("admin-pass-123"), role="admin"))
    db.commit()
    db.close()
    api = Api(_client())
    r = api.post("/api/auth/login", {"email": email, "password": "admin-pass-123"})
    assert r.status_code == 200, r.text
    api.csrf = r.json()["user"]["csrf_token"]
    api.user = r.json()["user"]
    return api
