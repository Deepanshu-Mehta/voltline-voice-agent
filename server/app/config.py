"""Runtime settings, read from environment variables or the repository's .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "server" / ".env"),
        extra="ignore",
    )

    # Paths
    content_dir: Path = REPO_ROOT / "content"
    db_path: Path = REPO_ROOT / "server" / "data" / "voltline.db"

    # LLM: any OpenAI-compatible Chat Completions endpoint (OpenAI, Gemini, Groq).
    llm_api_key: str | None = None
    openai_api_key: str | None = None  # used when llm_api_key is not set
    llm_base_url: str | None = None  # None = api.openai.com
    llm_model: str = "gpt-5.4-mini"
    llm_reasoning_effort: str | None = None  # e.g. "none" or "minimal" for GPT-5 family models
    llm_max_tokens: int = 300
    llm_timeout_s: float = 20.0
    judge_model: str = "gpt-5.4"  # evals only; a stronger model than the agent's

    # Agent behaviour (Standard mode defaults; Low Latency mode overrides these in Phase 3)
    history_turns: int = 10
    retrieval_top_k: int = 4
    max_tool_rounds: int = 3

    # Retrieval
    retrieval_dense: bool = True  # False = keyword (BM25) only
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # HTTP
    cors_origins: str = "http://localhost:3000"

    @property
    def resolved_llm_key(self) -> str | None:
        return self.llm_api_key or self.openai_api_key

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
