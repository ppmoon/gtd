from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Feishu
    feishu_app_id: str = ""
    feishu_app_secret: str = ""

    # Database
    db_path: str = "data/gtd.db"

    # Server
    host: str = "127.0.0.1"
    port: int = 8420

    # LLM (via LiteLLM)
    # Model format: provider/model, e.g. anthropic/claude-sonnet-4-6, openai/gpt-4o
    # Set to "mock" for keyword-based testing without API key
    llm_model: str = "mock"
    llm_api_key: str = ""
    llm_base_url: str = ""  # optional custom endpoint / proxy

    model_config = {"env_file": ".env"}


settings = Settings()  # type: ignore[call-arg]
