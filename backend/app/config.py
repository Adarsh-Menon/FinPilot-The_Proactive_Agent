from pathlib import Path
from typing import Literal
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All runtime configuration, read from environment variables and `.env`.

    Covers the database, model provider, keys and models, sessions, tracing, Grafana, Telegram alerts, model prices,
    news feeds and the master key location.

    Used by: create_app(), manage.py and the evaluation harness.
    """
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = "sqlite:///./runtime/finpilot.db"
    ops_database_url: str | None = None
    environment: str = "local"
    cookie_secure: bool = False
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8010,http://127.0.0.1:8010"
    inference_provider: Literal["mesh", "openrouter"] = "mesh"
    mesh_api_key: str = ""
    mesh_model: str = ""
    mesh_risk_model: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    openrouter_risk_model: str = ""
    # Speech to text for voice input; empty leaves voice to the browser's own recognition.
    mesh_speech_model: str = ""
    openrouter_speech_model: str = ""
    session_hours: int = 12
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "finpilot-local"
    langsmith_endpoint: str = ""
    grafana_url: str = "http://127.0.0.1:3000"
    # Phone alerts through a Telegram bot; both empty keeps alerts in the admin portal only.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Python of the separate evaluation environment; empty means .venv-evals in the repository.
    evals_python: str = ""
    # Everyday-spending model for the hybrid forecaster (a Chronos-Bolt id); empty uses the trailing-mean baseline.
    forecast_model: str = "amazon/chronos-bolt-small"
    # USD per million tokens, [input, output], per model id; used for the admin cost column.
    model_prices: dict[str, list[float]] = {}
    # Public market headline feeds for the investment agent (context only, never recommendations).
    news_feeds: list[str] = ["https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://www.livemint.com/rss/markets", "https://www.moneycontrol.com/rss/MCtopnews.xml"]
    # Master key outside the project, the database and synced folders; use a KMS in production.
    master_key_file: str = str(Path.home() / ".finpilot" / "master.key")

    @field_validator("database_url", "ops_database_url")
    @classmethod
    def anchor_sqlite(cls, value):
        # Relative SQLite paths resolve from the repository root, not the launch directory.
        """Resolve relative SQLite paths from the repository root, not the launch folder.

        Args:
            value: database URL.

        Returns:
            The URL with an absolute SQLite path.
        """
        prefix = "sqlite:///./"
        if value and value.startswith(prefix):
            return f"sqlite:///{(ROOT / value.removeprefix(prefix)).as_posix()}"
        return value

    def inference(self):
        """The selected provider's API key, shared model and risk model.

        Returns:
            (key, model, risk_model).

        Used by: create_app() when building the model gateway.
        """
        prefix = self.inference_provider
        return (getattr(self, f"{prefix}_api_key"), getattr(self, f"{prefix}_model"),
                getattr(self, f"{prefix}_risk_model"))

    def speech_model(self):
        """The selected provider's speech-to-text model, or empty.

        Returns:
            Model id string.

        Used by: The gateway's transcribe(); voice input.
        """
        return getattr(self, f"{self.inference_provider}_speech_model")

    def check(self):
        """Refuse to start outside local/test, and create the runtime folder for SQLite.

        Used by: create_app() and manage.py at startup.
        """
        if self.environment not in {"local", "test"}:
            raise ValueError("This development build is not approved for production deployment")
        if self.database_url.startswith("sqlite"):
            (ROOT / "runtime").mkdir(exist_ok=True)
