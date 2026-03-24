import os
from pathlib import Path


def _load_local_env_file() -> None:
    """Load apps/api/.env into process env if not already set."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
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
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./docflow.db",
    )
    upload_dir: str = os.getenv("UPLOAD_DIR", "./storage/uploads")
    auto_create_tables: bool = os.getenv(
        "AUTO_CREATE_TABLES", "true").lower() == "true"
    llm_provider: str = os.getenv("LLM_PROVIDER", "stub")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv(
        "ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
    anthropic_skills_enabled: bool = os.getenv(
        "ANTHROPIC_SKILLS_ENABLED", "true").lower() == "true"
    anthropic_skills_default_provider: bool = os.getenv(
        "ANTHROPIC_SKILLS_DEFAULT_PROVIDER", "true").lower() == "true"
    anthropic_skills_allow_fallback: bool = os.getenv(
        "ANTHROPIC_SKILLS_ALLOW_FALLBACK", "true").lower() == "true"
    anthropic_skills_timeout_seconds: int = int(
        os.getenv("ANTHROPIC_SKILLS_TIMEOUT_SECONDS", "90")
    )
    execution_backend: str = os.getenv("EXECUTION_BACKEND", "inline")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    queue_max_retries: int = int(os.getenv("QUEUE_MAX_RETRIES", "3"))
    queue_retry_delay_seconds: int = int(
        os.getenv("QUEUE_RETRY_DELAY_SECONDS", "5"))
    dead_letter_dir: str = os.getenv(
        "DEAD_LETTER_DIR", "./storage/dead_letter")
    ops_api_token: str = os.getenv("OPS_API_TOKEN", "")
    review_min_length: int = int(os.getenv("REVIEW_MIN_LENGTH", "120"))
    review_length_penalty: int = int(os.getenv("REVIEW_LENGTH_PENALTY", "20"))
    review_todo_penalty: int = int(os.getenv("REVIEW_TODO_PENALTY", "15"))
    review_required_keywords: str = os.getenv(
        "REVIEW_REQUIRED_KEYWORDS", "summary,conclusion")
    review_keyword_penalty: int = int(os.getenv("REVIEW_KEYWORD_PENALTY", "8"))
    review_required_threshold: int = int(
        os.getenv("REVIEW_REQUIRED_THRESHOLD", "90"))

    # Telegram
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_allowed_chat_ids: list[int] = [
        int(x.strip())
        for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
        if x.strip()
    ]
    telegram_webhook_url: str = os.getenv("TELEGRAM_WEBHOOK_URL", "")
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

    # Orchestrator
    agent_config_path: str = os.getenv("AGENT_CONFIG_PATH", "./config/agents.yaml")
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
    telegram_send_cooldown_seconds: float = float(
        os.getenv("TELEGRAM_SEND_COOLDOWN_SECONDS", "0.35")
    )
    telegram_identity_burst_limit: int = int(
        os.getenv("TELEGRAM_IDENTITY_BURST_LIMIT", "3")
    )
    telegram_identity_burst_window_seconds: float = float(
        os.getenv("TELEGRAM_IDENTITY_BURST_WINDOW_SECONDS", "2.0")
    )


settings = Settings()
