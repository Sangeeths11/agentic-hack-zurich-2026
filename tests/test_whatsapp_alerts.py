import asyncio
from datetime import datetime

import pytest
from support import FakeWhatsApp, assessment, simulation_settings

from scam_the_scamers.alerts import WhatsAppAlertManager, whatsapp_message
from scam_the_scamers.caller import CallerLookup
from scam_the_scamers.providers import ProviderError
from scam_the_scamers.risk import CRITERIA, Assessment, Scores


def configured_settings(**overrides):
    values = {"callmebot_api_key": "fake-key", "guardian_phone": "+12025550123"}
    values.update(overrides)
    return simulation_settings(**values)


@pytest.mark.parametrize(
    "scores,expected",
    [
        ([49], []),
        ([49, 50, 74, 75, 89, 90], [50, 75, 90]),
        ([90, 90, 50, 49, 75, 90], [90]),
        ([50, 49, 50, 75, 74, 75], [50, 75]),
    ],
)
async def test_whatsapp_tiers_once_per_session(scores, expected):
    settings = configured_settings()
    provider, events, notices = FakeWhatsApp(), [], []

    async def emit(*event):
        events.append(event)

    async def notify(event):
        notices.append(event)

    manager = WhatsAppAlertManager(
        settings, provider, settings.value("guardian_phone"), "session", emit, notify=notify
    )
    task = asyncio.create_task(manager.run())
    try:
        for score in scores:
            await manager.consider(assessment(score), CallerLookup("not_provided"))
            await asyncio.wait_for(manager.pending.join(), 1)
        assert len(provider.calls) == len(expected)
        assert [event["level"] for event in notices] == expected
        assert all(event["channel"] == "whatsapp" for event in notices)
        assert all(event["status"] == "accepted" for event in notices)
        assert all(datetime.fromisoformat(event["accepted_at"]).tzinfo for event in notices)
        assert manager.highest_accepted == max(expected, default=0)
        assert all(phone == settings.value("guardian_phone") for phone, _ in provider.calls)
        assert all("fake-key" not in text for _, text in provider.calls)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_whatsapp_content_uses_only_actual_evidence():
    result = Assessment(
        criteria=Scores(**{key: 84.0 if key == "urgency" else 0.0 for key in CRITERIA})
    )
    text = whatsapp_message(configured_settings(), 75, result, CallerLookup("no_match"))
    assert "RED ALERT" in text and "Score: 84/100" in text
    assert "Urgency: 84/100" in text
    for unsupported in [
        "Margaret",
        "Tyler",
        "34%",
        "CHF",
        "ALL criteria",
        "has been warned",
        "played",
        "listen in",
        "app.scamthescammer.ch",
        "Courier pickup:",
    ]:
        assert unsupported not in text
    assert "not a fraud probability" in text
    assert len(text) < 2048


@pytest.mark.parametrize("kind", ["scam", "number"])
async def test_whatsapp_independent_flags_and_unsent_tiers_coalesce(kind):
    settings = configured_settings()
    provider = FakeWhatsApp()

    async def emit(*event):
        pass

    manager = WhatsAppAlertManager(
        settings, provider, settings.value("guardian_phone"), "session", emit
    )
    caller = CallerLookup("matched" if kind == "number" else "not_provided")
    if kind == "number":
        await manager.consider_caller(caller)
    else:
        await manager.consider(assessment(10, is_scam=True), caller)
    level, text = manager.pending.get_nowait()
    manager.pending.task_done()
    assert level == 75
    if kind == "number":
        assert "score: not yet available" in text
    else:
        assert "Score: 10/100" in text and "independently flagged" in text
    for score in [50, 75, 90]:
        await manager.consider(assessment(score), caller)
    assert manager.pending.qsize() == 1
    assert manager.pending.get_nowait()[0] == 90
    assert not provider.calls


@pytest.mark.parametrize(
    "failure,status",
    [
        (ProviderError("callmebot", "credentials_rejected"), "error"),
        (ProviderError("callmebot", "request_failed", retryable=True), "error"),
        (ProviderError("callmebot", "transport_error"), "unknown"),
        (ProviderError("callmebot", "acceptance_unconfirmed"), "unknown"),
        (TimeoutError(), "unknown"),
    ],
)
async def test_whatsapp_failure_never_retries_ambiguous_send_or_announces_success(failure, status):
    settings = configured_settings()
    provider, events, notices = FakeWhatsApp(), [], []
    provider.failures = [failure]

    async def emit(*event):
        events.append(event)

    async def notify(event):
        notices.append(event)

    manager = WhatsAppAlertManager(
        settings, provider, settings.value("guardian_phone"), "session", emit, notify=notify
    )
    task = asyncio.create_task(manager.run())
    try:
        await manager.consider(assessment(75), CallerLookup("not_provided"))
        await asyncio.wait_for(manager.pending.join(), 1)
        await manager.consider(assessment(75), CallerLookup("not_provided"))
        assert len(provider.calls) == 1 and not notices
        assert events[-1][1] == status
        assert manager.highest_accepted == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("phone,key", [("", "fake"), ("garbage", "fake"), ("+12025550123", "")])
async def test_whatsapp_missing_or_invalid_configuration_never_sends(phone, key):
    settings = configured_settings(callmebot_api_key=key)
    provider, events = FakeWhatsApp(), []

    async def emit(*event):
        events.append(event)

    manager = WhatsAppAlertManager(settings, provider, phone, "session", emit)
    await manager.consider(assessment(90), CallerLookup("not_provided"))
    assert not provider.calls and manager.pending.empty()
    assert events[-1][1] in {"not_configured", "invalid_configuration"}
