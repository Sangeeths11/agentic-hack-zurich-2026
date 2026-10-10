"""Validated configuration; secrets stay out of reprs and logs."""

import re
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    twilio_account_sid: str = Field(default="", pattern=r"^(AC[0-9a-fA-F]{32})?$")
    twilio_auth_token: SecretStr = SecretStr("")
    twilio_phone_number: str = ""
    gradium_api_key: SecretStr = SecretStr("")
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = Field(default="gpt-4o-mini", min_length=1)
    public_http_url: str = ""
    operator_name: str = Field(default="Sangeeth", min_length=1, max_length=80)
    agent_language: str = "de"
    gradium_voice_id: str = ""
    gradium_base_url: str = ""
    test_phone_number: str = ""
    max_concurrent_calls: int = Field(default=2, ge=1, le=1000)
    max_call_seconds: float = Field(default=300, ge=10, le=2400)
    session_start_timeout_seconds: float = Field(default=15, ge=1, le=60)
    first_audio_timeout_seconds: float = Field(default=20, ge=1, le=60)
    provider_idle_timeout_seconds: float = Field(default=45, ge=1, le=120)
    media_idle_timeout_seconds: float = Field(default=20, ge=1, le=120)
    silence_timeout_seconds: float = Field(default=8, ge=3, le=30)

    @field_validator("public_http_url")
    @classmethod
    def https_origin(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError(
                    "PUBLIC_HTTP_URL must be an HTTPS origin without path or credentials"
                )
        return value

    @field_validator("twilio_phone_number", "test_phone_number")
    @classmethod
    def e164(cls, value: str) -> str:
        if value and not re.fullmatch(r"\+[1-9]\d{7,14}", value):
            raise ValueError("Phone numbers must use E.164, e.g. +41790000000")
        return value

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

    @property
    def websocket_url(self) -> str:
        return self.public_http_url.replace("https://", "wss://", 1) + "/twilio/stream"

    def missing(self) -> list[str]:
        names = (
            "twilio_account_sid",
            "twilio_auth_token",
            "twilio_phone_number",
            "gradium_api_key",
            "llm_api_key",
            "public_http_url",
        )
        return [name.upper() for name in names if not self.value(name)]

    def value(self, name: str) -> str:
        value = getattr(self, name)
        return value.get_secret_value() if isinstance(value, SecretStr) else str(value)
