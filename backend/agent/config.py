"""Runtime configuration, read once from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str = field(default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", ""))
    openrouter_base_url: str = field(
        default_factory=lambda: os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    )
    # Tiered routing: strong model for spec + codegen, cheap model for review + healing.
    strong_model: str = field(
        default_factory=lambda: os.environ.get("STRONG_MODEL", "anthropic/claude-sonnet-4.5")
    )
    cheap_model: str = field(default_factory=lambda: os.environ.get("CHEAP_MODEL", "openai/gpt-4o-mini"))
    fallback_model: str = field(
        default_factory=lambda: os.environ.get("FALLBACK_MODEL", "deepseek/deepseek-chat")
    )

    # "llm" lets the model write entity files; "template" is fully deterministic.
    codegen_mode: str = field(default_factory=lambda: os.environ.get("CODEGEN_MODE", "llm"))
    sandbox: str = field(default_factory=lambda: os.environ.get("SANDBOX", "local"))
    # Unprivileged OS user that runs generated code (used when the server runs as root).
    sandbox_user: str = field(default_factory=lambda: os.environ.get("SANDBOX_USER", "sandbox"))

    max_heal_iterations: int = field(default_factory=lambda: _env_int("MAX_HEAL_ITERATIONS", 3))
    max_tokens_per_run: int = field(default_factory=lambda: _env_int("MAX_TOKENS_PER_RUN", 60000))
    max_concurrent_runs: int = field(default_factory=lambda: _env_int("MAX_CONCURRENT_RUNS", 2))
    gate_timeout_s: int = field(default_factory=lambda: _env_int("GATE_TIMEOUT_S", 60))

    # FAKE_LLM=1: deterministic stand-in model, for UI development and offline demos.
    fake_llm: bool = field(default_factory=lambda: os.environ.get("FAKE_LLM", "") == "1")

    access_code: str = field(default_factory=lambda: os.environ.get("ACCESS_CODE", ""))
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
    )

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openrouter_api_key) or self.fake_llm

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def checkpoint_db(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"


settings = Settings()
