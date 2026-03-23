import os


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
    orchestrator_default_mode: str = os.getenv("ORCHESTRATOR_DEFAULT_MODE", "pipeline")
    orchestrator_max_turns: int = int(os.getenv("ORCHESTRATOR_MAX_TURNS", "6"))
    orchestrator_auto_summary: bool = os.getenv(
        "ORCHESTRATOR_AUTO_SUMMARY", "true").lower() == "true"


settings = Settings()
