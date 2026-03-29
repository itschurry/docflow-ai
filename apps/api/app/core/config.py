import os
from pathlib import Path

# Absolute project and API roots
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_API_ROOT = _PROJECT_ROOT / "apps" / "api"


def _resolve_path(value: str, *, base: Path | None = None) -> str:
    """Resolve relative filesystem paths as absolute paths."""
    raw = str(value or "").strip()
    if not raw:
        return raw
    candidate = Path(raw)
    if candidate.is_absolute():
        return str(candidate)
    root = base or _PROJECT_ROOT
    return str((root / raw).resolve())


def _load_local_env_file() -> None:
    """Load local .env files with precedence: apps/api/.env then project .env."""
    for env_path in (_API_ROOT / ".env", _PROJECT_ROOT / ".env"):
        if not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)


_load_local_env_file()


class Settings:
    app_name: str = os.getenv("APP_NAME", "DocFlow AI API")
    app_version: str = os.getenv("APP_VERSION", "0.2.0")

    # ── Path settings — all resolved to absolute paths ────────────────────────
    # SQLite URL: canonical default under project storage/db
    _raw_db_url: str = os.getenv("DATABASE_URL", "sqlite:///storage/db/docflow.db")
    database_url: str = (
        "sqlite:///" + _resolve_path(_raw_db_url[len("sqlite:///"):], base=_PROJECT_ROOT)
        if _raw_db_url.startswith("sqlite:///")
        else _raw_db_url
    )
    upload_dir: str = _resolve_path(os.getenv("UPLOAD_DIR", "storage/uploads"), base=_PROJECT_ROOT)
    agent_config_path: str = _resolve_path(os.getenv("AGENT_CONFIG_PATH", "apps/api/config/agents.yaml"), base=_PROJECT_ROOT)
    auto_create_tables: bool = os.getenv("AUTO_CREATE_TABLES", "true").lower() == "true"
    llm_provider: str = os.getenv("LLM_PROVIDER", "stub")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
    anthropic_skills_enabled: bool = os.getenv("ANTHROPIC_SKILLS_ENABLED", "true").lower() == "true"
    anthropic_skills_default_provider: bool = os.getenv("ANTHROPIC_SKILLS_DEFAULT_PROVIDER", "true").lower() == "true"
    anthropic_skills_allow_fallback: bool = os.getenv("ANTHROPIC_SKILLS_ALLOW_FALLBACK", "true").lower() == "true"
    anthropic_skills_timeout_seconds: int = int(os.getenv("ANTHROPIC_SKILLS_TIMEOUT_SECONDS", "90"))
    review_min_length: int = int(os.getenv("REVIEW_MIN_LENGTH", "120"))
    review_length_penalty: int = int(os.getenv("REVIEW_LENGTH_PENALTY", "20"))
    review_todo_penalty: int = int(os.getenv("REVIEW_TODO_PENALTY", "15"))
    review_required_keywords: str = os.getenv("REVIEW_REQUIRED_KEYWORDS", "summary,conclusion")
    review_keyword_penalty: int = int(os.getenv("REVIEW_KEYWORD_PENALTY", "8"))
    review_required_threshold: int = int(os.getenv("REVIEW_REQUIRED_THRESHOLD", "70"))

    # Orchestrator
    orchestrator_default_mode: str = os.getenv("ORCHESTRATOR_DEFAULT_MODE", "autonomous-lite")
    orchestrator_max_turns: int = int(os.getenv("ORCHESTRATOR_MAX_TURNS", "6"))
    orchestrator_same_agent_streak_limit: int = int(
        os.getenv("ORCHESTRATOR_SAME_AGENT_STREAK_LIMIT", "2")
    )
    orchestrator_recent_pattern_repeat_limit: int = int(
        os.getenv("ORCHESTRATOR_RECENT_PATTERN_REPEAT_LIMIT", "1")
    )
    orchestrator_max_no_progress_handoffs: int = int(
        os.getenv("ORCHESTRATOR_MAX_NO_PROGRESS_HANDOFFS", "2")
    )
    orchestrator_conversation_idle_timeout_minutes: int = int(
        os.getenv("ORCHESTRATOR_CONVERSATION_IDLE_TIMEOUT_MINUTES", "30")
    )
    orchestrator_auto_summary: bool = os.getenv(
        "ORCHESTRATOR_AUTO_SUMMARY", "true").lower() == "true"


settings = Settings()
