"""Validated local configuration; secrets and contacts stay out of reprs and logs."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_guardian_phone(value: str, region: str = "CH") -> str:
    """Validate one contact; prefer a valid national number over bare country digits."""
    import phonenumbers

    value = value.strip()
    if not value:
        return ""
    if len(value) > 64 or not re.fullmatch(r"[+0-9() .\-]+", value):
        raise ValueError("Use a valid guardian phone number including its country code")
    digits = re.sub(r"[() .\-]", "", value)
    if not re.fullmatch(r"\+?[0-9]+", digits):
        raise ValueError("Use a valid guardian phone number including its country code")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    candidates = [digits]
    # CallMeBot examples also use country digits without '+'. Try the user's
    # national numbering plan first so a Swiss 041... number stays Swiss.
    if not digits.startswith(("+", "0")):
        candidates.append("+" + digits)
    for candidate in candidates:
        try:
            parsed = phonenumbers.parse(candidate, region.upper())
        except phonenumbers.NumberParseException:
            continue
        if not parsed.extension and phonenumbers.is_valid_number(parsed):
            return str(phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164))
    raise ValueError("Use a valid guardian phone number including its country code")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    gradium_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    llm_model: str = Field(default="gemini-3.5-flash-lite", pattern=r"^gemini-[\w.\-]+$")
    operator_name: str = Field(default="You", min_length=1, max_length=80)
    agent_language: str = "en"
    gradium_voice_id: str = Field(default="", max_length=100)
    gradium_base_url: str = "https://api.gradium.ai"
    resend_api_key: SecretStr = SecretStr("")
    alert_from_email: SecretStr = SecretStr("")
    fallback_email: SecretStr = SecretStr("")
    callmebot_api_key: SecretStr = SecretStr("")
    callmebot_recipient_keys: SecretStr = SecretStr("")
    guardian_phone: SecretStr = SecretStr("")
    scam_db_path: Path | None = None
    caller_default_region: str = "CH"
    warning_threshold: float = Field(default=50, ge=0, le=100, allow_inf_nan=False)
    whatsapp_threshold: int = Field(default=50, ge=1, le=100)
    whatsapp_cooldown_seconds: int = Field(default=60, ge=0, le=300)
    whatsapp_debounce_seconds: float = Field(default=3, ge=0, le=10, allow_inf_nan=False)
    classification_interval_seconds: float = Field(default=1.5, ge=0.1, le=10)
    transcript_max_chars: int = Field(default=12000, ge=1000, le=24000)
    request_timeout_seconds: float = Field(default=20, ge=1, le=60)
    max_concurrent_sessions: int = Field(default=1, ge=1, le=10)
    max_session_seconds: float = Field(default=300, ge=10, le=2400)
    session_start_timeout_seconds: float = Field(default=15, ge=1, le=60)
    provider_idle_timeout_seconds: float = Field(default=45, ge=1, le=120)
    media_idle_timeout_seconds: float = Field(default=20, ge=1, le=120)

    @field_validator("llm_base_url")
    @classmethod
    def google_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "generativelanguage.googleapis.com"
            or parsed.path.rstrip("/") not in {"/v1beta/openai", "/v1/openai"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("LLM_BASE_URL must be Google's HTTPS OpenAI-compatible endpoint")
        return value.rstrip("/")

    @field_validator("gradium_base_url")
    @classmethod
    def gradium_url(cls, value: str) -> str:
        value = value or "https://api.gradium.ai"
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc not in {"api.gradium.ai", "eu.api.gradium.ai", "us.api.gradium.ai"}
            or parsed.path not in {"", "/", "/api", "/api/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("GRADIUM_BASE_URL must be a Gradium HTTPS endpoint")
        return f"https://{parsed.netloc}"

    @field_validator("agent_language")
    @classmethod
    def language(cls, value: str) -> str:
        if value not in {"de", "en", "fr", "es", "pt", "any"}:
            raise ValueError("Unsupported STT language")
        return value

    @field_validator("caller_default_region")
    @classmethod
    def region(cls, value: str) -> str:
        import phonenumbers

        value = value.upper()
        if value not in phonenumbers.SUPPORTED_REGIONS:
            raise ValueError("Use an ISO phone region such as CH")
        return value

    def value(self, name: str) -> str:
        value = getattr(self, name)
        return value.get_secret_value() if isinstance(value, SecretStr) else str(value)

    def gemini_key(self) -> str:
        return self.value("gemini_api_key") or self.value("llm_api_key")

    def missing(self) -> list[str]:
        missing = []
        if not self.value("gradium_api_key"):
            missing.append("GRADIUM_API_KEY")
        if not self.gemini_key():
            missing.append("GEMINI_API_KEY or LLM_API_KEY")
        return missing

    def email_status(self) -> str:
        from email_validator import EmailNotValidError, validate_email

        names = ("resend_api_key", "alert_from_email", "fallback_email")
        if not all(self.value(name) for name in names):
            return "not_configured"
        try:
            for name in names[1:]:
                validate_email(self.value(name), check_deliverability=False)
        except EmailNotValidError:
            return "invalid_configuration"
        return "configured"

    def whatsapp_status(self, phone: str | None = None) -> str:
        phone = self.value("guardian_phone") if phone is None else phone
        if not phone.strip():
            return "not_configured"
        try:
            normalize_guardian_phone(phone, self.caller_default_region)
        except ValueError:
            return "invalid_configuration"
        if self.key_for_phone(phone):
            return "configured"
        if self.value("callmebot_api_key") or self.value("callmebot_recipient_keys"):
            return "recipient_not_configured"
        return "not_configured"

    def key_for_phone(self, phone: str) -> str:
        """CallMeBot keys are recipient-bound; never reuse another contact's key."""
        try:
            canonical = normalize_guardian_phone(phone, self.caller_default_region)
        except ValueError:
            return ""
        if not canonical:
            return ""
        raw = self.value("callmebot_recipient_keys")
        if len(raw) > 8192:
            raw = ""
        try:
            mapping = json.loads(raw) if raw else {}
        except ValueError:
            mapping = {}
        if isinstance(mapping, dict) and len(mapping) <= 20:
            for number, key in mapping.items():
                if not isinstance(number, str) or not isinstance(key, str):
                    continue
                try:
                    normalized = normalize_guardian_phone(number, self.caller_default_region)
                except ValueError:
                    continue
                if normalized == canonical and key.strip() and len(key) <= 256:
                    return key.strip()
        try:
            original = normalize_guardian_phone(
                self.value("guardian_phone"), self.caller_default_region
            )
        except ValueError:
            original = ""
        return self.value("callmebot_api_key").strip() if canonical == original else ""
