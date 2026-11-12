"""Runtime settings, read from environment variables or the repository's .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# OpenAI-compatible Chat Completions endpoints. Each preset reads its own key variable, so
# switching provider is one line in .env: LLM_PROVIDER=groq | gemini | deepseek | openai.
LLM_PRESETS: dict[str, dict[str, str | None]] = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "groq_api_key",
             "model": "openai/gpt-oss-20b", "judge": "openai/gpt-oss-120b", "reasoning_effort": "low"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key_env": "gemini_api_key",
               "model": "gemini-3.5-flash-lite", "judge": "gemini-3.1-pro-preview", "reasoning_effort": "minimal"},
    "deepseek": {"base_url": "https://api.deepseek.com", "key_env": "deepseek_api_key",
                 "model": "deepseek-chat", "judge": "deepseek-chat", "reasoning_effort": None},
    "openai": {"base_url": None, "key_env": "openai_api_key",
               "model": "gpt-5.4-mini", "judge": "gpt-5.4", "reasoning_effort": None},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "server" / ".env"),
        extra="ignore",
    )

    # Paths
    content_dir: Path = REPO_ROOT / "content"
    db_path: Path = REPO_ROOT / "server" / "data" / "voltline.db"

    # LLM. Pick a preset; any field set explicitly below overrides the preset.
    llm_provider: str = "gemini"
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    deepseek_api_key: str | None = None
    openai_api_key: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_reasoning_effort: str | None = None
    llm_max_tokens: int = 600  # includes reasoning tokens on reasoning models
    llm_timeout_s: float = 20.0
    # None: no retry when a fallback provider is configured (switch at once), else 1. Raise for batch evals.
    llm_max_retries: int | None = None
    judge_model: str | None = None  # evals only; a stronger model than the agent's
    # Used when the main LLM fails before answering (rate limit, outage). Empty to disable.
    llm_fallback_provider: str | None = "deepseek"

    # Agent behaviour (Standard mode defaults; Low Latency mode overrides these in Phase 3)
    history_turns: int = 10
    retrieval_top_k: int = 4
    max_tool_rounds: int = 3

    # Retrieval
    retrieval_dense: bool = True  # False = keyword (BM25) only
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Speech-to-text (Deepgram, streaming)
    deepgram_api_key: str | None = None
    stt_model: str = "nova-3"
    stt_language: str = "en-US"
    stt_endpointing_ms: int = 700  # Standard mode silence before end of turn
    stt_utterance_end_ms: int = 1000  # backup end-of-turn signal, robust to background noise

    # Text-to-speech (Cartesia, streaming)
    cartesia_api_key: str | None = None
    cartesia_version: str = "2026-08-14"
    tts_model: str = "sonic-3.6"
    tts_voice_id: str = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"  # Jacqueline, en-US
    tts_sample_rate: int = 24000

    # HTTP
    cors_origins: str = "http://localhost:3000"

    # ---------------------------------------------------------------- resolved LLM settings

    @property
    def _preset(self) -> dict:
        return LLM_PRESETS.get(self.llm_provider, LLM_PRESETS["openai"])

    @property
    def resolved_llm_key(self) -> str | None:
        return self.llm_api_key or getattr(self, self._preset["key_env"], None)

    @property
    def resolved_llm_base_url(self) -> str | None:
        return self.llm_base_url or self._preset["base_url"]

    @property
    def resolved_llm_model(self) -> str:
        return self.llm_model or self._preset["model"]

    @property
    def resolved_judge_model(self) -> str:
        return self.judge_model or self._preset["judge"]

    @property
    def resolved_reasoning_effort(self) -> str | None:
        if self.llm_reasoning_effort is not None:
            return self.llm_reasoning_effort or None
        return self._preset["reasoning_effort"] if not self.llm_model else None

    @property
    def fallback_llm(self) -> tuple[str, str | None, str, str | None] | None:
        """(api key, base url, model, reasoning effort) of the fallback provider, if it has a key."""
        name = self.llm_fallback_provider
        if not name or name == self.llm_provider or name not in LLM_PRESETS:
            return None
        preset = LLM_PRESETS[name]
        key = getattr(self, preset["key_env"], None)
        if not key:
            return None
        return key, preset["base_url"], preset["model"], preset["reasoning_effort"]

    @property
    def voice_configured(self) -> bool:
        return bool(self.deepgram_api_key and self.cartesia_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
