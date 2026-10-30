"""Opt-in tests against a dedicated disposable database, never a user's database."""
import os
import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from backend.app.config import Settings, ROOT
from backend.app.storage import initialize, User, Profile, set_tenant
from backend.app.main import create_app, passwords
from conftest import register, setup_profile, import_history, ORIGIN

ADMIN_URL = os.environ.get("FINPILOT_TEST_POSTGRES_ADMIN_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="Dedicated PostgreSQL test URL not configured")


@pytest.fixture(scope="module")
def pg():
    from sqlalchemy.engine import make_url
    url = make_url(ADMIN_URL)
    if url.database != "finpilot_test":
        pytest.fail("Only the dedicated finpilot_test database is allowed")
    owner = create_engine(url)
    with owner.begin() as connection:
        for role in ("finpilot_app", "finpilot_ops"):
            exists = connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:name"), {"name":role})
            if not exists:
                connection.exec_driver_sql(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD 'local-integration-test-only'")
    initialize(owner)
    with owner.begin() as connection:
        connection.execute(text((ROOT/"backend/migrations/001_tenant_rls.sql").read_text()))
    app_url = url.set(username="finpilot_app", password="local-integration-test-only").render_as_string(hide_password=False)
    ops_url = url.set(username="finpilot_ops", password="local-integration-test-only").render_as_string(hide_password=False)
    app_engine, ops_engine = create_engine(app_url), create_engine(ops_url)
    yield owner, app_engine, ops_engine, app_url, ops_url
    owner.dispose();app_engine.dispose();ops_engine.dispose()


def test_database_rls_and_operations_privileges(pg):
    owner, engine, ops, _, _ = pg
    from uuid import uuid4
    a,b=str(uuid4()),str(uuid4())
    with Session(owner) as db:
        db.add_all([User(id=a,username="a_"+a[:20],password_hash="fixture"),User(id=b,username="b_"+b[:20],password_hash="fixture")]);db.flush()
        db.add_all([Profile(tenant_id=a,data={"private":"A"}),Profile(tenant_id=b,data={"private":"B"})]);db.commit()
    with Session(engine) as db:
        role=db.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
        assert tuple(role)==(False,False)
        assert db.scalars(select(Profile)).all()==[]
        set_tenant(db,a)
        assert [p.tenant_id for p in db.scalars(select(Profile))]==[a]
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE profiles SET tenant_id=:b WHERE tenant_id=:a"),{"a":a,"b":b})
        db.rollback()
        assert db.scalars(select(Profile)).all()==[]
    with ops.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM policy"))==1
        for table in ("users","sessions","profiles","transactions","imports","runs"):
            with pytest.raises(DBAPIError):
                connection.execute(text(f"SELECT * FROM {table}"))
            connection.rollback()


def test_application_with_restricted_postgres_roles(pg):
    _, _, _, app_url, ops_url = pg
    app=create_app(Settings(environment="test",database_url=app_url,ops_database_url=ops_url))
    from uuid import uuid4
    with TestClient(app,headers=ORIGIN) as client:
        register(client,"pg_"+uuid4().hex[:12]);setup_profile(client);import_history(client)
        run=client.post("/api/v1/forecasts",json={})
        assert run.status_code==200,run.text
        assert run.json()["forecast"]["timeline"][-1]["cash_minor"]==14000
        assert client.get("/ops/v1/overview").status_code==401


def test_app_rejects_privileged_database_configuration(pg):
    _, _, _, _, ops_url=pg
    app=create_app(Settings(environment="test",database_url=ADMIN_URL,ops_database_url=ops_url))
    with pytest.raises(ValueError,match="must not bypass"):
        with TestClient(app,headers=ORIGIN):
            pass
