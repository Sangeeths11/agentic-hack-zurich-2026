"""Regression checks for live settings races, using only in-memory provider doubles."""

import asyncio
import json

import pytest
from support import DemoFactory, assessment, next_event, simulation_settings

from scam_the_scamers.caller import CallerLookup
from scam_the_scamers.profiles import EmergencyContact, PersonalProfile, RuntimePreferences
from scam_the_scamers.providers import TranscriptEvent

OLD_PHONE = "+41791234567"
NEW_PHONE = "+12025550123"


def preferences(revision, phone=None):
    contacts = (EmergencyContact(name="Emergency contact", phone=phone),) if phone else ()
    return RuntimePreferences(
        revision=revision,
        profile=PersonalProfile(emergency_contacts=contacts),
        whatsapp_cooldown_seconds=0,
    )


def configured_settings():
    return simulation_settings(
        guardian_phone=OLD_PHONE,
        callmebot_api_key="synthetic-old-key",
        callmebot_recipient_keys=json.dumps({NEW_PHONE: "synthetic-new-key"}),
    )


@pytest.mark.asyncio
async def test_provider_start_resolves_latest_profile_before_any_alert_worker_runs():
    factory = DemoFactory(configured_settings(), auto_text=False)
    opening, release = asyncio.Event(), asyncio.Event()
    original_open = factory.stt_open

    async def delayed_open():
        opening.set()
        await release.wait()
        return await original_open()

    factory.stt_open = delayed_open
    current = preferences(1, OLD_PHONE)
    pending = asyncio.create_task(
        factory.open(
            CallerLookup("matched", "+12013759200", "synthetic fixture"),
            "profile-startup-race",
            preferences=current,
            preferences_provider=lambda: current,
        )
    )
    await asyncio.wait_for(opening.wait(), 1)
    current = preferences(2, NEW_PHONE)
    release.set()
    session = await asyncio.wait_for(pending, 1)
    try:
        await asyncio.wait_for(factory.whatsapp.started.wait(), 1)
        assert session.preferences.revision == 2
        assert [phone for phone, _ in factory.whatsapp.calls] == [NEW_PHONE]
        assert not factory.classifier.calls
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_adding_contact_reconsiders_known_number_without_waiting_for_speech():
    factory = DemoFactory(configured_settings(), auto_text=False)
    session = await factory.open(
        CallerLookup("matched", "+12013759200", "synthetic fixture"),
        "profile-number-only",
        preferences=preferences(1),
    )
    try:
        async with asyncio.timeout(1):
            while True:
                event = await session.receive()
                if event.payload.get("tool") == "callmebot":
                    assert event.payload["status"] == "not_configured"
                    break
        assert not session.transcript.text
        assert not factory.whatsapp.calls
        await session.update_preferences(preferences(2, NEW_PHONE))
        await asyncio.wait_for(factory.whatsapp.started.wait(), 1)
        assert [phone for phone, _ in factory.whatsapp.calls] == [NEW_PHONE]
        assert "Conversation score: not yet available" in factory.whatsapp.calls[0][1]
        assert not factory.classifier.calls
        assert not factory.speech.calls
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_profile_change_during_blocked_publication_cannot_send_old_assessment():
    settings = configured_settings().model_copy(
        update={
            "resend_api_key": "synthetic-email-key",
            "alert_from_email": "sender@example.com",
            "fallback_email": "contact@example.com",
        }
    )
    factory = DemoFactory(settings, assessment(0, warning=True, is_scam=True), auto_text=False)
    old_preferences = preferences(1, OLD_PHONE)
    session = await factory.open(
        CallerLookup("not_provided"), "profile-publication-race", preferences=old_preferences
    )
    held, release, fresh_started, fresh_release = (asyncio.Event() for _ in range(4))
    original_activity = session.activity
    original_classify = factory.classifier.classify
    classifications = 0

    async def activity(tool, status, detail):
        if tool == "scoring" and status == "complete":
            held.set()
            await release.wait()
        await original_activity(tool, status, detail)

    async def classify(*args, **kwargs):
        nonlocal classifications
        classifications += 1
        if classifications > 1:
            fresh_started.set()
            await fresh_release.wait()
        return await original_classify(*args, **kwargs)

    session.activity = activity
    factory.classifier.classify = classify
    try:
        await factory.stts[0].events.put(TranscriptEvent("text", "Synthetic old family context."))
        await asyncio.wait_for(held.wait(), 1)
        await session.update_preferences(preferences(2, NEW_PHONE))
        release.set()
        await asyncio.wait_for(fresh_started.wait(), 1)
        # The earlier profile's scam judgment must not be sent to either recipient.
        assert not factory.whatsapp.calls
        assert not factory.email.calls
        assert not factory.speech.calls
        assert session.latest is None
        assert session.preferences.revision == 2
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_profile_change_before_synthesis_does_not_call_tts_with_old_assessment():
    factory = DemoFactory(result=assessment(75, warning=True), auto_text=False)
    session = await factory.open(
        CallerLookup("not_provided"), "profile-tts-race", preferences=preferences(1)
    )
    held, release = asyncio.Event(), asyncio.Event()
    original_activity = session.activity

    async def activity(tool, status, detail):
        if tool == "tts" and status == "synthesizing":
            held.set()
            await release.wait()
        await original_activity(tool, status, detail)

    session.activity = activity
    try:
        await factory.stts[0].events.put(TranscriptEvent("text", "Synthetic family context."))
        await asyncio.wait_for(held.wait(), 1)
        factory.classifier.result = assessment(20)
        await session.update_preferences(preferences(2))
        release.set()
        while (await next_event(session, "assessment")).payload["settings_revision"] != 2:
            pass
        await asyncio.wait_for(session.warning_queue.join(), 1)
        assert not factory.speech.calls
        assert session.warning_attempted == 0
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_settings_change_discards_warning_audio_already_waiting_for_browser():
    factory = DemoFactory(result=assessment(75, warning=True), auto_text=False)
    session = await factory.open(
        CallerLookup("not_provided"), "profile-buffered-warning", preferences=preferences(1)
    )
    ready = asyncio.Event()
    original_activity = session.activity

    async def activity(tool, status, detail):
        await original_activity(tool, status, detail)
        if tool == "tts" and status == "ready":
            ready.set()

    session.activity = activity
    try:
        await factory.stts[0].events.put(TranscriptEvent("text", "Synthetic family context."))
        await asyncio.wait_for(ready.wait(), 1)
        assert len(factory.speech.calls) == 1
        await session.update_preferences(preferences(2))
        async with asyncio.timeout(1):
            while True:
                event = await session.receive()
                assert not event.audio
                if event.payload.get("event") == "settings_applied":
                    assert event.payload["revision"] == 2
                    break
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_session_uses_configured_whatsapp_threshold_without_profile_override():
    settings = configured_settings().model_copy(update={"whatsapp_threshold": 90})
    factory = DemoFactory(settings, result=assessment(75), auto_text=False)
    session = await factory.open(CallerLookup("not_provided"), "configured-threshold")
    try:
        await factory.stts[0].events.put(TranscriptEvent("text", "Synthetic score-only context."))
        await asyncio.wait_for(session.finish(), 2)
        assert session.whatsapp_alerts.preferences.whatsapp_threshold == 90
        assert not factory.whatsapp.calls
    finally:
        await session.close()
