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


def personal_preferences(*, phones=("+12025550123",), revision=1, **values):
    from scam_the_scamers.profiles import EmergencyContact, PersonalProfile, RuntimePreferences

    return RuntimePreferences(
        profile=PersonalProfile(
            listener_name="Margaret",
            personal_context="My daughter is Emily. My son is Jonas.",
            emergency_contacts=tuple(
                EmergencyContact(
                    phone=phone,
                    name="Emily" if index == 0 else "Jonas",
                    relationship="daughter" if index == 0 else "son",
                )
                for index, phone in enumerate(phones)
            ),
        ),
        revision=revision,
        **values,
    )


async def silent_emit(*event):
    pass


def described(score, summary):
    return assessment(score).model_copy(update={"conversation_summary": summary})


async def test_burst_coalesces_highest_tier_with_latest_conversation_summary():
    settings = configured_settings(whatsapp_debounce_seconds=0.04)
    provider = FakeWhatsApp()
    manager = WhatsAppAlertManager(
        settings, provider, "", "session", silent_emit, preferences=personal_preferences()
    )
    task = asyncio.create_task(manager.run())
    try:
        await manager.consider(
            described(50, "The caller claims to need help."), CallerLookup("no_match")
        )
        await asyncio.sleep(0.01)
        await manager.consider(
            described(75, "The caller asks for a bank transfer."), CallerLookup("no_match")
        )
        await asyncio.sleep(0.01)
        await manager.consider(
            described(90, "The caller now requests urgent cash collection."),
            CallerLookup("no_match"),
        )
        await asyncio.wait_for(manager.pending.join(), 1)
        assert len(provider.calls) == 1
        phone, message = provider.calls[0]
        assert phone == "+12025550123"
        assert "CRITICAL" in message and "urgent cash collection" in message
        assert "Call screening for Margaret" in message and "For Emily (daughter)" in message
        assert "bank transfer" not in message
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_cooldown_coalesces_latest_summary_then_sends_after_gap():
    settings = configured_settings()
    provider = FakeWhatsApp()
    manager = WhatsAppAlertManager(
        settings, provider, "", "session", silent_emit, preferences=personal_preferences()
    )
    task = asyncio.create_task(manager.run())
    try:
        await manager.consider(
            described(50, "Initial suspicious request."), CallerLookup("no_match")
        )
        await asyncio.wait_for(manager.pending.join(), 1)
        await manager.consider(described(75, "New pressure to act."), CallerLookup("no_match"))
        await asyncio.sleep(0.01)
        await manager.consider(
            described(90, "The caller now asks for cash pickup."), CallerLookup("no_match")
        )
        await asyncio.sleep(0.01)
        assert len(provider.calls) == 1
        # Advance only the recorded recipient time, avoiding a real minute-long test.
        manager.last_attempt["+12025550123"] -= 61
        await manager.consider(
            described(90, "The caller repeats the cash pickup request."), CallerLookup("no_match")
        )
        await asyncio.wait_for(manager.pending.join(), 1)
        assert len(provider.calls) == 2 and "CRITICAL" in provider.calls[-1][1]
        assert "repeats the cash pickup" in provider.calls[-1][1]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_finish_flushes_initial_burst_but_discards_cooldown_followups():
    settings = configured_settings(whatsapp_debounce_seconds=3)
    provider = FakeWhatsApp()
    manager = WhatsAppAlertManager(
        settings, provider, "", "session", silent_emit, preferences=personal_preferences()
    )
    task = asyncio.create_task(manager.run())
    try:
        await manager.consider(assessment(50), CallerLookup("no_match"))
        # Finishing never spends three seconds waiting for the initial debounce.
        await manager.finish()
        await asyncio.wait_for(manager.pending.join(), 0.5)
        assert len(provider.calls) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    manager = WhatsAppAlertManager(
        configured_settings(), provider, "", "next", silent_emit, preferences=personal_preferences()
    )
    task = asyncio.create_task(manager.run())
    try:
        await manager.consider(assessment(50), CallerLookup("no_match"))
        await asyncio.wait_for(manager.pending.join(), 1)
        await manager.consider(assessment(90), CallerLookup("no_match"))
        await asyncio.sleep(0.01)
        await manager.finish()
        await asyncio.wait_for(manager.pending.join(), 0.5)
        assert len(provider.calls) == 2  # One initial alert in each separate session.
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_live_contact_change_discards_obsolete_pending_recipient():
    import json

    settings = configured_settings(
        whatsapp_debounce_seconds=0.04,
        callmebot_recipient_keys=json.dumps({"+12025550124": "jonas-key"}),
    )
    provider = FakeWhatsApp()
    manager = WhatsAppAlertManager(
        settings, provider, "", "session", silent_emit, preferences=personal_preferences()
    )
    task = asyncio.create_task(manager.run())
    try:
        await manager.consider(assessment(90), CallerLookup("no_match"))
        await asyncio.sleep(0.01)
        await manager.update_preferences(personal_preferences(phones=("+12025550124",), revision=2))
        await manager.consider(
            described(90, "Fresh assessment after the settings change."), CallerLookup("no_match")
        )
        await asyncio.wait_for(manager.pending.join(), 1)
        assert [phone for phone, _ in provider.calls] == ["+12025550124"]
        assert "Fresh assessment" in provider.calls[0][1]
        await manager.update_preferences(personal_preferences(phones=(), revision=3))
        await manager.consider(assessment(90), CallerLookup("no_match"))
        assert manager.pending.empty() and len(provider.calls) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_personal_contacts_fan_out_once_and_report_recipient_identity():
    import json

    settings = configured_settings(
        callmebot_recipient_keys=json.dumps({"+12025550124": "jonas-key"})
    )
    provider, notices = FakeWhatsApp(), []

    async def notify(event):
        notices.append(event)

    manager = WhatsAppAlertManager(
        settings,
        provider,
        "",
        "session",
        silent_emit,
        preferences=personal_preferences(phones=("+12025550123", "+12025550124", "+12025550125")),
        notify=notify,
    )
    task = asyncio.create_task(manager.run())
    try:
        await manager.consider(assessment(90), CallerLookup("no_match"))
        await asyncio.wait_for(manager.pending.join(), 1)
        await manager.consider(assessment(90), CallerLookup("no_match"))
        assert [phone for phone, _ in provider.calls] == ["+12025550123", "+12025550124"]
        assert [notice["contact_name"] for notice in notices] == ["Emily", "Jonas"]
        assert [notice["contact_phone"] for notice in notices] == ["+12025550123", "+12025550124"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "threshold,score,flag,expected", [(30, 35, False, 50), (95, 90, False, 0), (95, 10, True, 75)]
)
async def test_custom_whatsapp_threshold_and_independent_scam_warning(
    threshold, score, flag, expected
):
    settings, provider = configured_settings(), FakeWhatsApp()
    manager = WhatsAppAlertManager(
        settings,
        provider,
        "",
        "session",
        silent_emit,
        preferences=personal_preferences(whatsapp_threshold=threshold),
    )
    await manager.consider(assessment(score, is_scam=flag), CallerLookup("no_match"))
    if expected:
        level, _ = manager.pending.get_nowait()
        manager.pending.task_done()
        assert level == expected
    else:
        assert manager.pending.empty()


@pytest.mark.parametrize(
    "threshold,score,flag,expected", [(30, 35, False, 50), (95, 90, False, 0), (95, 10, True, 75)]
)
async def test_server_whatsapp_threshold_applies_without_personal_preferences(
    threshold, score, flag, expected
):
    settings = configured_settings(whatsapp_threshold=threshold)
    manager = WhatsAppAlertManager(
        settings, FakeWhatsApp(), settings.value("guardian_phone"), "session", silent_emit
    )
    await manager.consider(assessment(score, is_scam=flag), CallerLookup("no_match"))
    if expected:
        level, _ = manager.pending.get_nowait()
        manager.pending.task_done()
        assert level == expected
    else:
        assert manager.pending.empty()


async def test_server_whatsapp_cooldown_applies_without_personal_preferences():
    settings = configured_settings(whatsapp_cooldown_seconds=60)
    provider, coalescing = FakeWhatsApp(), asyncio.Event()

    async def emit(tool, status, detail):
        if status == "coalescing":
            coalescing.set()

    manager = WhatsAppAlertManager(
        settings, provider, settings.value("guardian_phone"), "session", emit
    )
    task = asyncio.create_task(manager.run())
    try:
        await manager.consider(assessment(50), CallerLookup("no_match"))
        await asyncio.wait_for(manager.pending.join(), 1)
        await manager.consider(assessment(90), CallerLookup("no_match"))
        await asyncio.wait_for(coalescing.wait(), 1)
        assert len(provider.calls) == 1
        await manager.finish()
        await asyncio.wait_for(manager.pending.join(), 1)
        assert len(provider.calls) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_stale_preferences_cannot_restore_a_removed_contact_or_discard_fresh_work():
    provider = FakeWhatsApp()
    manager = WhatsAppAlertManager(
        configured_settings(),
        provider,
        "",
        "session",
        silent_emit,
        preferences=personal_preferences(),
    )
    await manager.consider(assessment(90), CallerLookup("no_match"))
    await manager.update_preferences(personal_preferences(phones=(), revision=2))
    await manager.update_preferences(personal_preferences(revision=1))
    await manager.update_preferences(personal_preferences(revision=2))
    await manager.consider(assessment(90), CallerLookup("no_match"))
    assert manager.pending.empty() and not provider.calls
    assert manager.preferences == personal_preferences(phones=(), revision=2)

    await manager.update_preferences(personal_preferences(revision=3))
    await manager.consider(assessment(90), CallerLookup("no_match"))
    await manager.update_preferences(personal_preferences(phones=(), revision=2))
    task = asyncio.create_task(manager.run())
    try:
        await asyncio.wait_for(manager.pending.join(), 1)
        assert [phone for phone, _ in provider.calls] == ["+12025550123"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_contact_removal_while_sending_activity_is_blocked_prevents_request():
    provider, entered, release = FakeWhatsApp(), asyncio.Event(), asyncio.Event()

    async def emit(tool, status, detail):
        if status == "sending":
            entered.set()
            await release.wait()

    manager = WhatsAppAlertManager(
        configured_settings(), provider, "", "session", emit, preferences=personal_preferences()
    )
    task = asyncio.create_task(manager.run())
    try:
        await manager.consider(assessment(90), CallerLookup("no_match"))
        await asyncio.wait_for(entered.wait(), 1)
        await manager.update_preferences(personal_preferences(phones=(), revision=2))
        release.set()
        await asyncio.wait_for(manager.pending.join(), 1)
        assert not provider.calls and not manager.attempted and not manager.last_attempt
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_personalized_message_keeps_action_and_timestamp_with_maximum_fields():
    from scam_the_scamers.profiles import EmergencyContact, PersonalProfile

    result = assessment(100, True, is_scam=True).model_copy(
        update={
            "conversation_summary": "S" * 600,
            "profile_mismatches": ["D" * 160] * 3,
        }
    )
    contact = EmergencyContact(name="E" * 80, relationship="R" * 80, phone="+12025550123")
    profile = PersonalProfile(listener_name="M" * 80, emergency_contacts=(contact,))
    text = whatsapp_message(
        configured_settings(), 90, result, CallerLookup("matched"), profile=profile, contact=contact
    )
    assert len(text) <= 1900
    assert "NOW. Verify the caller independently." in text
    assert text.endswith(".") and "Screening alert at " in text
