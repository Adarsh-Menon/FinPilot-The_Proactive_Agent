import json

import httpx
import requests
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.observability import Telemetry, sanitize
from backend.app.storage import Policy
from conftest import ORIGIN, register, setup_profile, import_history
from test_categorization import completion, response_for


class Capture(requests.adapters.BaseAdapter):
    """Records LangSmith export requests in-process; nothing leaves the test."""
    def __init__(self):
        super().__init__()
        self.bodies = []

    def send(self, request, **kwargs):
        self.bodies.append(request.body if isinstance(request.body, bytes) else (request.body or "").encode())
        response = requests.Response()
        response.status_code, response._content, response.request, response.url = 200, b"{}", request, request.url
        response.headers["Content-Type"] = "application/json"
        return response

    def close(self):
        pass


def captured_telemetry():
    capture = Capture()
    telemetry = Telemetry("lsv2-synthetic-test-key", "finpilot-test", True, endpoint="https://langsmith.invalid")
    for prefix in ("https://", "http://"):
        telemetry.client.session.mount(prefix, capture)
    return telemetry, capture


def test_sanitize_keeps_only_allowlisted_fields():
    data = {"agent": "categorizer", "batch_size": 2, "description": "Cafe", "transactions": [{"amount_minor": -100}],
        "profile": {"opening_balance_minor": 5}, "usage_metadata": {"input_tokens": 5, "note": "private"}}
    assert sanitize(data) == {"agent": "categorizer", "batch_size": 2, "usage_metadata": {"input_tokens": 5}}
    assert sanitize(None) == {} and sanitize(["not", "a", "dict"]) == {}


def test_metrics_endpoint_uses_route_templates_and_no_private_data(client):
    register(client); setup_profile(client); import_history(client)
    run = client.post("/api/v1/forecasts", json={}).json()
    assert client.get(f"/api/v1/runs/{run['id']}").status_code == 200
    assert client.post("/api/v1/auth/login", headers={"Origin": "https://evil.example"},
        json={"username": "customer_a", "password": "synthetic-password-123"}).status_code == 403
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    text = metrics.text
    assert 'route="/api/v1/runs/{run_id}"' in text
    assert 'finpilot_http_requests_total{method="POST",route="unmatched",status="403"}' in text
    assert 'finpilot_agent_node_duration_seconds_count{node="analysis",status="ok"}' in text
    assert 'finpilot_categorizations_total{source="unresolved",status="not_configured"}' in text
    assert 'finpilot_policy_mode{mode="AI_PAUSED"} 1.0' in text
    assert 'finpilot_auth_attempts_total{action="register",outcome="success"}' in text
    for private in ["customer_a", "Cafe meal", run["id"], "synthetic-password-123"]:
        assert private not in text


def test_langsmith_export_contains_agent_structure_but_no_financial_content(tmp_path):
    def mesh(request):
        return completion(json.dumps(response_for(request, "dining")))
    telemetry, capture = captured_telemetry()
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'trace.db'}", environment="test",
        mesh_api_key="test-key", mesh_model="test-default"), mesh_transport=httpx.MockTransport(mesh), telemetry=telemetry)
    with TestClient(app, headers=ORIGIN) as client:
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = "RUNNING"
            db.commit()
        register(client); setup_profile(client); import_history(client)
        run = client.post("/api/v1/forecasts", json={}).json()
        assert client.get("/health").json()["tracing_enabled"] is True
    exported = b"".join(capture.bodies).decode(errors="ignore")
    for node in ["analysis", "projection", "checks_node", "categorizer"]:
        assert node in exported
    for private in ["Cafe meal", "customer_a", "dining", run["id"], "amount_minor", "opening_balance",
            "test-key", "Equipment purchase"]:
        assert private not in exported


def test_tracing_disabled_by_default_overrides_ambient_environment(monkeypatch, tmp_path):
    from langchain_core.tracers.langchain import LangChainTracer
    created = []
    original = LangChainTracer.__init__
    def spy(self, *args, **kwargs):
        created.append(True)
        original(self, *args, **kwargs)
    monkeypatch.setattr(LangChainTracer, "__init__", spy)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'off.db'}", environment="test", mesh_api_key="", mesh_model=""))
    with TestClient(app, headers=ORIGIN) as client:
        assert client.get("/health").json()["tracing_enabled"] is False
        register(client); setup_profile(client); import_history(client)
        assert client.post("/api/v1/forecasts", json={}).status_code == 200
    assert created == []


def test_cost_uses_configured_prices_and_leaves_unknown_models_unpriced():
    from backend.app.observability import priced
    prices = {"deepseek/deepseek-v4-flash": [0.05, 0.45]}
    assert priced("deepseek/deepseek-v4-flash", 3138, 369, prices) == 0.00032295
    assert priced("qwen/other", 100, 100, prices) is None and priced(None, 1, 1, prices) is None
    assert priced("deepseek/deepseek-v4-flash", None, 5, prices) is None
