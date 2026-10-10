import asyncio
from unittest.mock import AsyncMock

import pytest
from support import FakeClassifier, FakeSpeech, FakeSTT, simulation_settings

from scam_the_scamers import cli
from scam_the_scamers.providers import ProviderError, TranscriptEvent


async def test_live_doctor_never_sends_guardian_messages_even_when_configured(monkeypatch):
    whatsapp_send = AsyncMock(side_effect=AssertionError("Must not contact a guardian"))
    email_send = AsyncMock(side_effect=AssertionError("Must not contact a guardian"))
    monkeypatch.setattr("scam_the_scamers.providers.CallMeBotWhatsApp.send", whatsapp_send)
    monkeypatch.setattr("scam_the_scamers.providers.ResendEmail.send", email_send)
    stt = FakeSTT(auto_text=False)
    await stt.events.put(TranscriptEvent("text", "Synthetic diagnostic."))
    speech = FakeSpeech()
    speech.synthesize = AsyncMock(return_value=b"\0" * 1920)
    result = await cli.check_providers(
        simulation_settings(
            callmebot_api_key="fake",
            guardian_phone="+41791234567",
            resend_api_key="fake",
            alert_from_email="sender@example.com",
            fallback_email="caregiver@example.com",
        ),
        classifier=FakeClassifier(),
        speech=speech,
        stt_open=AsyncMock(return_value=stt),
    )
    assert result["status"] == "passed"
    whatsapp_send.assert_not_called()
    email_send.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [None, "credentials", "timeout", "tts", "stt_open", "no_transcript", "stt_stream", "cleanup"],
)
async def test_provider_doctor_status_and_cleanup_with_injected_providers(failure):
    classifier = FakeClassifier()
    speech = FakeSpeech()
    speech.synthesize = AsyncMock(return_value=b"\0" * 1920)
    stt = FakeSTT(auto_text=False)
    opener = AsyncMock(return_value=stt)
    if failure != "no_transcript":
        await stt.events.put(TranscriptEvent("text", "Synthetic diagnostic."))
    if failure == "credentials":
        classifier.error = ProviderError("gemini", "credentials_rejected")
    elif failure == "timeout":
        classifier.error = TimeoutError("private request URL or credential")
    elif failure == "tts":
        speech.synthesize.side_effect = ProviderError("tts", "request_failed")
    elif failure == "stt_open":
        opener.side_effect = ProviderError("stt", "setup_failed")
    elif failure == "stt_stream":
        stt.receive = AsyncMock(side_effect=ProviderError("stt", "stream_failed"))
    elif failure == "cleanup":
        stt.close = AsyncMock(side_effect=RuntimeError("private cleanup details"))
    result = await cli.check_providers(
        simulation_settings(), classifier=classifier, speech=speech, stt_open=opener
    )
    assert result["status"] == ("failed" if failure else "passed")
    assert "never sends email" in result["email"]
    assert "never sends WhatsApp" in result["whatsapp"]
    assert "private" not in str(result)
    if failure not in {"tts", "stt_open", "cleanup"}:
        assert stt.closed
    if failure == "no_transcript":
        assert result["stt"] == {"status": "no transcript", "segments": 0}
    elif failure == "credentials":
        assert result["family_only"] == "gemini: credentials_rejected"
    elif failure is None:
        assert result["stt"]["status"] == "passed"
        assert result["tts"]["status"] == "passed"
        assert result["family_only"]["status"] == result["scam_scenario"]["status"] == "passed"
    # A failed receive must not leave the feeder running after cleanup.
    assert not any(t.get_coro().__name__ == "feed" for t in asyncio.all_tasks())


@pytest.mark.parametrize("status,exit_code", [("passed", 0), ("failed", 1)])
def test_doctor_live_exit_code(monkeypatch, capsys, status, exit_code):
    monkeypatch.setattr(cli, "Settings", simulation_settings)
    monkeypatch.setattr("sys.argv", ["scam-the-scamers", "doctor", "--live"])
    probe = AsyncMock(return_value={"status": status, "email": "not tested"})
    monkeypatch.setattr(cli, "check_providers", probe)
    if exit_code:
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == exit_code
    else:
        assert cli.main() is None
    assert f'"status": "{status}"' in capsys.readouterr().out
    probe.assert_awaited_once()
