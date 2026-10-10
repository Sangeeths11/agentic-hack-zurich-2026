from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    twilio_account_sid: str = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    twilio_auth_token: str = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    twilio_phone_number: str = os.environ.get("TWILIO_PHONE_NUMBER", "").strip()
    destination_phone_number: str = os.environ.get("DESTINATION_PHONE_NUMBER", "").strip()
    public_http_url: str = os.environ.get("PUBLIC_HTTP_URL", "").strip().rstrip("/")
    operator_name: str = os.environ.get("OPERATOR_NAME", "Family Shield").strip()
    allow_insecure_local: bool = _env_flag("ALLOW_INSECURE_LOCAL")


settings = Settings()
