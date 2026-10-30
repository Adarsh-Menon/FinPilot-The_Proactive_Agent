"""The LLM judge for DeepEval metrics, called through the same model gateway as the app.

The judge must not be the model under test: it defaults to the risk model (Qwen) because most graded
agents use the shared model, and EVAL_JUDGE_MODEL overrides it. EVAL_JUDGE=off disables judge metrics.
"""
import json
import os
import re

from deepeval.models import DeepEvalBaseLLM
from openai import OpenAI

from backend.app.config import Settings
from backend.app.mesh import FAST_REPLY, PROVIDER_URLS


def judge_enabled():
    """True unless EVAL_JUDGE=off; code-based checks always run."""
    return os.environ.get("EVAL_JUDGE", "on").lower() != "off"


class GatewayJudge(DeepEvalBaseLLM):
    """DeepEval judge model backed by the configured gateway.

    Inputs: none (reads provider, key and models from `.env`; EVAL_JUDGE_MODEL picks another judge).
    Output: an object DeepEval metrics (GEval, Faithfulness, Answer Relevancy) call to score test cases.
    Used by: the forecaster and goals-coach evaluations in `evals/deepeval/`.
    """

    def __init__(self):
        settings = Settings()
        key, model, risk_model = settings.inference()
        self.provider = settings.inference_provider
        self.model_id = os.environ.get("EVAL_JUDGE_MODEL") or risk_model
        if self.model_id == model:
            raise ValueError("The judge must differ from the model under test; set EVAL_JUDGE_MODEL")
        self.client = OpenAI(api_key=key, base_url=PROVIDER_URLS[self.provider], max_retries=2)
        super().__init__(self.model_id)

    def load_model(self):
        """Return the OpenAI-compatible client DeepEval keeps as the loaded model."""
        return self.client

    def generate(self, prompt, schema=None):
        """Ask the judge; with `schema` (a pydantic model) return a parsed instance, else plain text."""
        if schema is not None:
            prompt += "\n\nReply with only a JSON object matching this schema:\n" + json.dumps(schema.model_json_schema())
        response = self.client.chat.completions.create(model=self.model_id, temperature=0, max_tokens=2000,
            messages=[{"role": "user", "content": prompt}], extra_body=FAST_REPLY.get(self.provider, {}),
            **({"response_format": {"type": "json_object"}} if schema is not None else {}))
        text = response.choices[0].message.content or ""
        if schema is None:
            return text
        match = re.search(r"\{.*\}", text, re.S)
        return schema.model_validate_json(match.group(0) if match else text)

    async def a_generate(self, prompt, schema=None):
        """Async form DeepEval calls when metrics run concurrently; delegates to `generate`."""
        return self.generate(prompt, schema)

    def get_model_name(self):
        """Name shown in DeepEval reports."""
        return f"gateway-judge:{self.model_id}"
