from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM (OpenRouter)
    openrouter_api_key: str = ""
    openai_api_key: str = ""
    llm_model_primary: str = "qwen/qwen3-235b-a22b-2507"
    llm_model_secondary: str = "deepseek/deepseek-v3.2"
    llm_model_fallback: str = "openai/gpt-4o-mini"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # WhatsApp Bridge
    whatsapp_bridge_url: str = "http://whatsapp-bridge:3000"
    whatsapp_bridge_token: str = "newsbot-local-bridge-token"  # Required for bridge/webhook authentication
    whatsapp_number: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/newsbot.db"

    # Scheduler (4 pipelines por dia)
    pipeline_hours: str = "7,12,17,21"  # horas de envio separadas por vírgula
    timezone: str = "America/Sao_Paulo"

    # Admin
    admin_phone: str = ""
    admin_auth_enabled: bool = True
    admin_username: str = "admin"
    admin_password: str = ""

    # Allowed numbers (whitelist) - comma separated, e.g. "5511999999999,5511888888888"
    # Empty means allow all
    allowed_numbers: str = ""

    # App
    log_level: str = "INFO"
    send_rate_limit: float = 1.0

    @property
    def base_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def pipeline_hours_list(self) -> list[int]:
        """Hours (0–23) for morning, midday, afternoon, evening pipelines in order."""
        raw_hours = [h.strip() for h in self.pipeline_hours.split(",") if h.strip()]
        if len(raw_hours) != 4:
            raise ValueError("pipeline_hours must contain exactly 4 comma-separated hours")

        try:
            hours = [int(hour) for hour in raw_hours]
        except ValueError as exc:
            raise ValueError("pipeline_hours values must be integers") from exc

        if any(hour < 0 or hour > 23 for hour in hours):
            raise ValueError("pipeline_hours values must be between 0 and 23")

        return hours

    @property
    def pipeline_schedule_display(self) -> str:
        """Human-readable send times, e.g. \"07:00 / 12:00 / 17:00 / 21:00\"."""
        return " / ".join(f"{h:02d}:00" for h in self.pipeline_hours_list)

    @property
    def pipeline_schedule_display_br(self) -> str:
        """Same hours with \"h\" suffix for WhatsApp copy (matches scheduler order)."""
        return ", ".join(f"{h}h" for h in self.pipeline_hours_list)


settings = Settings()
