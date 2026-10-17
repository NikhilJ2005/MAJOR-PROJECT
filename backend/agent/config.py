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


# Every provider speaks the OpenAI chat API, so switching is just a different URL, key and models.
# name: (base URL, API-key variable, strong model, cheap model, fallback model)
PROVIDERS: dict[str, tuple[str, str, str, str, str]] = {
    # Default. Gemini 4 Argon (paid tier only) designs the app and writes the business logic; Gemini 3.8
    # Flash does the quick jobs. FREE_TIER=1 uses 3.8 Flash for everything. `python scripts/check_llm.py
    # --list` shows the model ids your key can use.
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY",
               "gemini-4-argon", "gemini-3.8-flash", "gemini-3.8-flash"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY",
             "openai/gpt-oss-120b", "openai/gpt-oss-20b", "openai/gpt-oss-20b"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                   "anthropic/claude-sonnet-4.6", "qwen/qwen3-coder", "deepseek/deepseek-chat-v3.1"),
    "openai_compatible": ("", "LLM_API_KEY", "", "", ""),
}


def _provider() -> str:
    """LLM_PROVIDER, or else the first provider whose API key is set (Gemini first)."""
    chosen = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if chosen in PROVIDERS:
        return chosen
    for name, (_, key_var, *_) in PROVIDERS.items():
        if os.environ.get(key_var):
            return name
    return "gemini"


def _from_provider(index: int, override: str) -> str:
    """A provider default (URL/model) that an environment variable may override."""
    if index == 2 and _provider() == "gemini" and os.environ.get("FREE_TIER") == "1":
        index = 3  # Gemini 4 Argon has no free tier: build with Flash instead
    return os.environ.get(override) or PROVIDERS[_provider()][index]


@dataclass(frozen=True)
class Settings:
    llm_provider: str = field(default_factory=_provider)
    llm_base_url: str = field(default_factory=lambda: _from_provider(0, "LLM_BASE_URL"))
    llm_api_key: str = field(
        default_factory=lambda: os.environ.get(PROVIDERS[_provider()][1]) or os.environ.get("LLM_API_KEY", "")
    )
    # Tiered routing: strong model for design + code, cheap model for the first repairs.
    strong_model: str = field(default_factory=lambda: _from_provider(2, "STRONG_MODEL"))
    cheap_model: str = field(default_factory=lambda: _from_provider(3, "CHEAP_MODEL"))
    fallback_model: str = field(default_factory=lambda: _from_provider(4, "FALLBACK_MODEL"))
    # Free tiers allow few tokens per minute: call the model fewer times at once, and retry 429s.
    llm_max_parallel: int = field(
        default_factory=lambda: _env_int("LLM_MAX_PARALLEL", {"openrouter": 4, "gemini": 4}.get(_provider(), 1))
    )
    # How hard each tier thinks (Gemini thinking level / OpenAI reasoning effort): low, medium or high.
    # Empty sends nothing (the model's default).
    strong_reasoning: str = field(default_factory=lambda: os.environ.get("STRONG_REASONING", "high"))
    cheap_reasoning: str = field(default_factory=lambda: os.environ.get("CHEAP_REASONING", "low"))
    llm_max_retries: int = field(default_factory=lambda: _env_int("LLM_MAX_RETRIES", 6))

    # "llm" lets the model write entity files; "template" is fully deterministic.
    codegen_mode: str = field(default_factory=lambda: os.environ.get("CODEGEN_MODE", "llm"))
    sandbox: str = field(default_factory=lambda: os.environ.get("SANDBOX", "local"))
    # Unprivileged OS user that runs generated code (used when the server runs as root).
    sandbox_user: str = field(default_factory=lambda: os.environ.get("SANDBOX_USER", "sandbox"))

    # Healing stops when it stops making progress (see healing.should_continue);
    # this is only a seatbelt against a pathological run.
    max_heal_safety: int = field(default_factory=lambda: _env_int("MAX_HEAL_SAFETY", 12))
    # The budget per build is money: thinking models make token counts unpredictable.
    max_cost_per_run_usd: float = field(default_factory=lambda: float(os.environ.get("MAX_COST_PER_RUN_USD", "3.0")))
    max_tokens_per_run: int = field(default_factory=lambda: _env_int("MAX_TOKENS_PER_RUN", 2_000_000))
    max_concurrent_runs: int = field(default_factory=lambda: _env_int("MAX_CONCURRENT_RUNS", 2))
    gate_timeout_s: int = field(default_factory=lambda: _env_int("GATE_TIMEOUT_S", 60))

    # FAKE_LLM=1: deterministic stand-in model, for UI development and offline demos.
    fake_llm: bool = field(default_factory=lambda: os.environ.get("FAKE_LLM", "") == "1")

    # Public deployments: ACCESS_CODE is an invite code needed to create an account.
    access_code: str = field(default_factory=lambda: os.environ.get("ACCESS_CODE", ""))
    max_runs_per_day: int = field(default_factory=lambda: _env_int("MAX_RUNS_PER_DAY", 25))
    admin_emails: frozenset[str] = field(
        default_factory=lambda: frozenset(
            e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
        )
    )
    # Resource limits for generated code (see sandbox.LocalSandbox.spawn).
    sandbox_memory_mb: int = field(default_factory=lambda: _env_int("SANDBOX_MEMORY_MB", 2048))
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
    )

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key) or self.fake_llm

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def checkpoint_db(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    @property
    def platform_db(self) -> Path:
        return self.data_dir / "platform.sqlite"

    @property
    def platform_database_url(self) -> str:
        """Accounts, projects and run state: SQLite in DATA_DIR, or Postgres (PLATFORM_DATABASE_URL)."""
        url = os.environ.get("PLATFORM_DATABASE_URL", "")
        if not url:
            return f"sqlite:///{self.platform_db}"
        # Railway gives postgres:// or postgresql://; SQLAlchemy needs the psycopg (v3) driver named.
        return url.replace("postgres://", "postgresql://", 1).replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def uses_postgres(self) -> bool:
        return self.platform_database_url.startswith("postgresql")


settings = Settings()
