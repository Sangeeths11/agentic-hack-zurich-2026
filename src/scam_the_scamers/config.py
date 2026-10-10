"""Validated configuration; secrets stay out of reprs and logs."""

from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    gradium_api_key: SecretStr = SecretStr("")
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = Field(default="gpt-4o-mini", min_length=1)
    operator_name: str = Field(default="Sangeeth", min_length=1, max_length=80)
    agent_language: str = "en"
    gradium_voice_id: str = ""
    gradium_base_url: str = ""
    max_concurrent_sessions: int = Field(default=1, ge=1, le=100)
    max_session_seconds: float = Field(default=300, ge=10, le=2400)
    session_start_timeout_seconds: float = Field(default=15, ge=1, le=60)
    first_audio_timeout_seconds: float = Field(default=20, ge=1, le=60)
    provider_idle_timeout_seconds: float = Field(default=45, ge=1, le=120)
    media_idle_timeout_seconds: float = Field(default=20, ge=1, le=120)
    silence_timeout_seconds: float = Field(default=8, ge=3, le=30)

    @field_validator("llm_base_url", "gradium_base_url")
    @classmethod
    def provider_url(cls, value: str, info) -> str:
        if not value and info.field_name == "gradium_base_url":
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "Provider URL must be an HTTP(S) endpoint without credentials or query"
            )
        return value.rstrip("/")

    @field_validator("agent_language")
    @classmethod
    def language(cls, value: str) -> str:
        if value not in {"de", "en", "fr", "es", "pt"}:
            raise ValueError("AGENT_LANGUAGE must be de, en, fr, es or pt")
        return value

    def missing(self) -> list[str]:
        names = ("gradium_api_key", "llm_api_key")
        return [name.upper() for name in names if not self.value(name)]

    def value(self, name: str) -> str:
        value = getattr(self, name)
        return value.get_secret_value() if isinstance(value, SecretStr) else str(value)
