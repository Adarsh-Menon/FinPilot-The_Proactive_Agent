import json
import shutil
import pytest
import httpx
from backend.app.config import ROOT
from backend.app.prompts import PromptCatalog
from backend.app.mesh import MeshGateway, NumericContext, enforce_origin


def test_runtime_prompt_checksums_and_composition(tmp_path):
    shutil.copytree(ROOT/"prompts",tmp_path/"prompts")
    catalog=PromptCatalog(tmp_path/"prompts")
    prompt,metadata=catalog.compose("advisor")
    assert "Shared system instructions" in prompt and "Advisor" in prompt
    assert metadata["prompt_version"]=="v6"
    path=tmp_path/"prompts/advisor/v6.md"
    path.write_text(path.read_text()+"tampered")
    with pytest.raises(ValueError,match="checksum"):
        catalog.compose("advisor")


def test_mesh_fixed_origin_mock_and_no_redirects():
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={"id":"synthetic","object":"chat.completion","created":0,"model":"synthetic-test-model","choices":[{"index":0,"message":{"role":"assistant","content":"{}"},"finish_reason":"stop"}]})
    gateway=MeshGateway("synthetic-test-key","synthetic-test-model",PromptCatalog(ROOT/"prompts"),lambda:"RUNNING",httpx.MockTransport(handler))
    context=NumericContext(currency="INR",horizon_days=30,daily_variable_minor=100)
    try:
        result=gateway.evaluate("advisor",context)
        assert len(requests)==1
        assert str(requests[0].url)=="https://api.meshapi.ai/v1/chat/completions"
        assert result["unvalidated_output"]=="{}"
        assert gateway.http.follow_redirects is False
        assert "synthetic-test-key" not in json.dumps(result)
    finally:
        gateway.close()


def test_mesh_pause_or_missing_configuration_prevents_dispatch():
    def forbidden(_):
        raise AssertionError("Unexpected dispatch")
    gateway=MeshGateway("", "", PromptCatalog(ROOT/"prompts"),lambda:"AI_PAUSED",httpx.MockTransport(forbidden))
    try:
        with pytest.raises(ValueError,match="paused"):
            gateway.evaluate("advisor",NumericContext(currency="INR",horizon_days=30,daily_variable_minor=100))
    finally:
        gateway.close()


@pytest.mark.parametrize("url",["https://api.openai.com/v1/chat/completions","http://api.meshapi.ai/v1/chat/completions","https://api.meshapi.ai:444/v1/chat/completions","https://api.meshapi.ai/other"])
def test_inference_origin_allowlist(url):
    with pytest.raises(ValueError):
        enforce_origin(httpx.Request("POST",url))


def test_openrouter_provider_uses_its_fixed_origin():
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={"id":"synthetic","object":"chat.completion","created":0,"model":"synthetic-test-model","choices":[{"index":0,"message":{"role":"assistant","content":"{}"},"finish_reason":"stop"}]})
    gateway=MeshGateway("synthetic-test-key","synthetic-test-model",PromptCatalog(ROOT/"prompts"),lambda:"RUNNING",httpx.MockTransport(handler),provider="openrouter")
    try:
        gateway.evaluate("advisor",NumericContext(currency="INR",horizon_days=30,daily_variable_minor=100))
        assert str(requests[0].url)=="https://openrouter.ai/api/v1/chat/completions"
        assert gateway.inspect_configuration()["provider"]=="openrouter"
        with pytest.raises(ValueError,match="OPENROUTER_API_KEY and OPENROUTER_RISK_MODEL"):
            gateway.model_for("risk")
    finally:
        gateway.close()


@pytest.mark.parametrize("url",["https://api.meshapi.ai/v1/chat/completions","https://openrouter.ai/v1/chat/completions","http://openrouter.ai/api/v1/chat/completions"])
def test_openrouter_origin_allowlist(url):
    with pytest.raises(ValueError):
        enforce_origin(httpx.Request("POST",url),"https://openrouter.ai/api/v1")
