import asyncio

import pytest
from support import FakeEmail, assessment, simulation_settings
from test_providers import email_settings

from scam_the_scamers.alerts import AlertManager
from scam_the_scamers.caller import CallerLookup
from scam_the_scamers.providers import ProviderError


@pytest.mark.parametrize(
    "scores,expected",
    [
        ([49], []),
        ([49, 50, 74, 75, 89, 90], [50, 75, 90]),
        ([90, 90, 50, 49, 75, 90], [90]),
        ([50, 49, 50, 75, 74, 75], [50, 75]),
    ],
)
async def test_alert_boundaries_jumps_and_jitter(scores, expected):
    email = FakeEmail()
    events = []

    async def emit(*event):
        events.append(event)

    manager = AlertManager(email_settings(), email, "synthetic-session", emit)
    task = asyncio.create_task(manager.run())
    try:
        for score in scores:
            await manager.consider(assessment(score), CallerLookup("not_provided"))
            await asyncio.wait_for(manager.pending.join(), 1)
        assert [int(key.rsplit("/", 1)[1]) for _, key in email.calls] == expected
        assert manager.highest_attempted == max(expected, default=0)
        assert manager.highest_accepted == max(expected, default=0)
        for body, _ in email.calls:
            assert "transcript" not in body
            assert "Family-member claim" in body["text"]
            assert "not a probability" in body["text"]
        assert all(event[1] != "delivered" for event in events)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "failures,attempts,accepted",
    [
        ([ProviderError("resend", retryable=True)], 2, 50),
        ([TimeoutError(), TimeoutError()], 2, 0),
        ([ProviderError("resend", "credentials_rejected")], 1, 0),
    ],
)
async def test_delivery_retries_stable_key_payload_and_no_repeat_spam(failures, attempts, accepted):
    email = FakeEmail()
    email.failures = failures.copy()
    events = []

    async def emit(*event):
        events.append(event)

    manager = AlertManager(email_settings(), email, "session", emit)
    task = asyncio.create_task(manager.run())
    try:
        await manager.consider(assessment(50), CallerLookup("not_provided"))
        await asyncio.wait_for(manager.pending.join(), 3)
        await manager.consider(assessment(49), CallerLookup("not_provided"))
        await manager.consider(assessment(50), CallerLookup("not_provided"))
        assert len(email.calls) == attempts
        assert all(call == email.calls[0] for call in email.calls)
        assert manager.highest_attempted == 50 and manager.highest_accepted == accepted
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_unconfigured_email_and_pending_tiers_coalesce():
    events = []

    async def emit(*event):
        events.append(event)

    email = FakeEmail()
    manager = AlertManager(simulation_settings(), email, "session", emit)
    await manager.consider(assessment(90), CallerLookup("not_provided"))
    assert not email.calls and manager.highest_accepted == 0
    assert events[-1][1] == "not_configured"
    manager = AlertManager(email_settings(), email, "session", emit)
    for score in [50, 75, 90]:
        await manager.consider(assessment(score), CallerLookup("not_provided"))
    assert manager.pending.qsize() == 1
    assert manager.pending.get_nowait()[0] == 90
