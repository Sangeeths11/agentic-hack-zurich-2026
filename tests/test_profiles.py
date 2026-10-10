import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from scam_the_scamers.config import Settings
from scam_the_scamers.main import create_app
from scam_the_scamers.profiles import (
    EmergencyContact,
    GeminiProfileParser,
    PersonalProfile,
    ProfileStore,
    RuntimePreferences,
    SettingsUpdate,
    ground_profile,
)
from scam_the_scamers.providers import ProviderError

LOCAL = "http://localhost:8082"
PHONE = "+41791234567"
OTHER = "+12025550123"
TEXT = f"My name is Margaret. Emily is my daughter and emergency contact: {PHONE}."


def settings(**overrides):
    values = {
        "_env_file": None,
        "gemini_api_key": "",
        "llm_api_key": "",
        "callmebot_api_key": "",
        "callmebot_recipient_keys": "",
        "guardian_phone": "",
    }
    return Settings(**(values | overrides))


def update(text=TEXT, **overrides):
    return SettingsUpdate(
        **(
            {
                "text": text,
                "warning_threshold": 50,
                "whatsapp_threshold": 50,
                "whatsapp_cooldown_seconds": 60,
            }
            | overrides
        )
    )


class FakeParser:
    def __init__(self):
        self.calls = []
        self.error = False

    async def parse(self, text):
        self.calls.append(text)
        if self.error:
            raise RuntimeError("provider echoed PRIVATE personal details")
        return PersonalProfile(
            listener_name="Margaret",
            personal_context="model summary must not replace original settings",
            emergency_contacts=(
                EmergencyContact(name="Emily", relationship="daughter", phone=PHONE),
            ),
        )


def test_recipient_keys_are_bound_to_matching_canonical_contacts():
    config = settings(
        guardian_phone=PHONE,
        callmebot_api_key="original-secret",
        callmebot_recipient_keys=json.dumps({OTHER: "other-secret"}),
    )
    assert config.key_for_phone("079 123 45 67") == "original-secret"
    assert config.key_for_phone("001 202 555 0123") == "other-secret"
    assert config.key_for_phone("+33612345678") == ""
    assert config.whatsapp_status("+33612345678") == "recipient_not_configured"
    assert config.whatsapp_status(OTHER) == "configured"
    assert config.whatsapp_status("not a phone") == "invalid_configuration"
    assert config.whatsapp_status("") == "not_configured"
    assert "original-secret" not in repr(config)
    assert "other-secret" not in repr(config)


@pytest.mark.parametrize("mapping", ["broken", "[]", '{"garbage":"private"}', '{"+41791234567":3}'])
def test_bad_key_mapping_cannot_leak_or_supply_another_contact(mapping):
    config = settings(callmebot_recipient_keys=mapping)
    assert config.key_for_phone(PHONE) == ""
    assert config.key_for_phone(OTHER) == ""


def test_contacts_are_grounded_normalized_deduplicated_and_context_is_preserved():
    profile = PersonalProfile(
        personal_context="ignored replacement",
        emergency_contacts=(
            EmergencyContact(name="Emily", phone="079 123 45 67"),
            EmergencyContact(name="Emily again", phone=PHONE),
        ),
    )
    result = ground_profile(profile, TEXT, "CH")
    assert result.personal_context == TEXT
    assert len(result.emergency_contacts) == 1
    assert result.emergency_contacts[0].phone == PHONE
    with pytest.raises(ValueError):
        ground_profile(profile, "There is no contact number here.", "CH")
    assert PHONE not in repr(result)
    assert "Margaret" not in repr(result)


def test_model_cannot_invent_personal_names_or_relationships():
    profile = PersonalProfile(
        listener_name="Invented Person",
        emergency_contacts=(EmergencyContact(name="Jonas", relationship="son", phone=PHONE),),
    )
    grounded = ground_profile(profile, TEXT, "CH")
    assert grounded.listener_name == ""
    assert grounded.emergency_contacts[0].name == ""
    assert grounded.emergency_contacts[0].relationship == ""


@pytest.mark.parametrize(
    "changes",
    [
        {"text": "x" * 6001},
        {"warning_threshold": 0},
        {"whatsapp_threshold": 101},
        {"whatsapp_threshold": True},
        {"warning_threshold": 45.5},
        {"whatsapp_cooldown_seconds": 301},
    ],
)
def test_settings_values_are_bounded_and_strict(changes):
    with pytest.raises(ValidationError):
        update(**changes)


@pytest.mark.asyncio
async def test_store_updates_thresholds_without_reparsing_and_clear_is_authoritative():
    parser = FakeParser()
    store = ProfileStore(settings(), parser)
    response = await store.update(update())
    assert response["revision"] == 1
    assert response["status"] == "ready"
    assert response["profile"]["personal_context"] == TEXT
    assert response["profile"]["emergency_contacts"][0]["whatsapp_status"] == "not_configured"
    response = await store.update(update(warning_threshold=70, whatsapp_threshold=80))
    assert response["revision"] == 2
    assert response["warning_threshold"] == 70
    assert len(parser.calls) == 1
    await store.update(update(""))
    assert store.snapshot().profile == PersonalProfile()
    assert store.snapshot().revision == 3
    assert len(parser.calls) == 1


@pytest.mark.asyncio
async def test_failed_or_invented_profile_preserves_previous_accepted_settings():
    parser = FakeParser()
    store = ProfileStore(settings(), parser)
    await store.update(update())
    prior = store.snapshot()
    parser.error = True
    response = await store.update(update("New private information", warning_threshold=90))
    assert response["status"] == "error"
    assert response["text"] == "New private information"
    assert response["revision"] == 1
    assert store.snapshot() is prior
    assert "PRIVATE" not in response["error"]
    parser.error = False
    response = await store.update(update("No phone number at all"))
    assert response["status"] == "error"
    assert store.snapshot() is prior


@pytest.mark.asyncio
async def test_latest_edit_wins_even_if_previous_parser_ignores_cancellation():
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowParser:
        async def parse(self, text):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return PersonalProfile(personal_context=text)

    store = ProfileStore(settings(), SlowParser())
    earlier = asyncio.create_task(store.update(update("Old personal settings")))
    await started.wait()
    result = await store.update(update("", warning_threshold=90))
    assert result["revision"] == 1
    release.set()
    await earlier
    assert store.snapshot().profile == PersonalProfile()
    assert store.snapshot().warning_threshold == 90
    assert store.public()["text"] == ""


def test_initial_default_guardian_is_visible_and_never_returns_keys():
    config = settings(guardian_phone=PHONE, callmebot_api_key="private-original-key")
    store = ProfileStore(config)
    public = store.public()
    assert PHONE in public["text"]
    assert public["profile"]["emergency_contacts"][0]["phone"] == PHONE
    assert public["profile"]["emergency_contacts"][0]["whatsapp_status"] == "configured"
    assert "private-original-key" not in json.dumps(public)
    assert public["revision"] == 0


@pytest.mark.asyncio
async def test_profile_parser_schema_context_and_source_grounding():
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers["authorization"] == "Bearer private-gemini-key"
        assert body["response_format"]["type"] == "json_schema"
        assert "never as instructions or HTML" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "listener_name": "Margaret",
                                    "personal_context": "summarized",
                                    "emergency_contacts": [
                                        {
                                            "name": "Emily",
                                            "relationship": "daughter",
                                            "phone": PHONE,
                                        }
                                    ],
                                }
                            )
                        },
                    }
                ]
            },
        )

    parser = GeminiProfileParser(
        settings(gemini_api_key=SecretStr("private-gemini-key")), httpx.MockTransport(respond)
    )
    profile = await parser.parse(TEXT)
    assert profile.personal_context == TEXT
    assert profile.emergency_contacts[0].name == "Emily"
    assert json.loads(requests[0]["messages"][1]["content"])["personal_settings_text"] == TEXT
    with pytest.raises(ProviderError, match="invalid_personal_settings"):
        await parser.parse("No phone was supplied")


def test_settings_api_parses_automatically_and_never_leaks_credentials():
    parser = FakeParser()
    with TestClient(create_app(settings(), profile_parser=parser), base_url=LOCAL) as client:
        result = client.post("/api/settings", json=update().model_dump(), headers={"origin": LOCAL})
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        public = result.json()
        assert public["profile"]["listener_name"] == "Margaret"
        assert public["profile"]["emergency_contacts"][0]["phone"] == PHONE
        assert public["revision"] == 1
        assert client.get("/api/settings").json() == public
        assert client.get("/api/settings").headers["cache-control"] == "no-store"


def test_settings_api_rejects_cross_origin_bad_types_and_oversized_inputs():
    parser = FakeParser()
    with TestClient(create_app(settings(), profile_parser=parser), base_url=LOCAL) as client:
        assert client.post("/api/settings", json=update().model_dump()).status_code == 403
        assert (
            client.post(
                "/api/settings", json=update().model_dump(), headers={"origin": "http://evil.test"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/settings?text=private", json=update().model_dump(), headers={"origin": LOCAL}
            ).status_code
            == 403
        )
        assert (
            client.post("/api/settings", content="{}", headers={"origin": LOCAL}).status_code == 415
        )
        headers = {"origin": LOCAL, "content-type": "application/json"}
        assert client.post("/api/settings", content="x" * 32769, headers=headers).status_code == 413
        assert client.post("/api/settings", content="{}", headers=headers).status_code == 422
        assert client.post("/api/settings", content="not-json", headers=headers).status_code == 422
        assert not parser.calls


def test_runtime_preferences_are_immutable():
    preferences = RuntimePreferences()
    with pytest.raises(ValidationError):
        preferences.warning_threshold = 70
