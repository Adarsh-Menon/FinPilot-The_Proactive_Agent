import os
import tempfile
from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient
from backend.app.config import Settings
from backend.app.main import create_app

ORIGIN = {"Origin": "http://localhost:5173"}
# Tests must never pick up a developer's local .env credentials or tracing settings,
# nor the developer's real master key.
Settings.model_config["env_file"] = None
os.environ["MASTER_KEY_FILE"] = os.path.join(tempfile.gettempdir(), "finpilot-test-master.key")
os.environ["NEWS_FEEDS"] = "[]"  # never fetch live news in tests
os.environ["FORECAST_MODEL"] = ""  # the baseline forecaster; the hybrid is tested with a stand-in model


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", environment="test", mesh_api_key="", mesh_model=""))


@pytest.fixture
def client(app):
    with TestClient(app, headers=ORIGIN) as client:
        yield client


def register(client, name="customer_a"):
    response = client.post("/api/v1/auth/register", json={"username": name, "password": "synthetic-password-123"})
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()


def setup_profile(client, balance=10000):
    profile = {"currency": "INR", "timezone": "Asia/Dubai", "as_of": "2026-10-18", "coverage_start": "2026-08-20", "coverage_confirmed": True,
        "opening_balance_minor": balance, "reserve_minor": 3000,
        "scheduled_flows": [{"date": "2026-10-19", "label": "confirmed income", "amount_minor": 10000}, {"date": "2026-10-20", "label": "confirmed rent", "amount_minor": -3000}]}
    version = client.get("/api/v1/profile").json()["version"]
    result = client.put("/api/v1/profile", json={"profile": profile, "expected_version": version})
    assert result.status_code == 200, result.text
    return profile


def import_history(client):
    lines = ["date,description,amount_minor,currency,kind"]
    for offset in range(60):
        lines.append(f"{date(2026,8,20)+timedelta(days=offset)},Cafe meal,-100,INR,expense")
    preview = client.post("/api/v1/imports", json={"csv_text": "\n".join(lines)})
    assert preview.status_code == 201, preview.text
    p = preview.json()
    response = client.post(f"/api/v1/imports/{p['id']}/confirm", json={"expected_version":p["version"]})
    assert response.status_code == 200, response.text
    return p
