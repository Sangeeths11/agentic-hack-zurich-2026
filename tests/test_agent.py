import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import gradbot
from support import simulation_settings

from scam_the_scamers.agent import GradbotFactory, GradbotSession, session_config


def test_pinned_native_sdk_accepts_german_session_configuration():
    config = session_config(simulation_settings())
    assert config.language == gradbot.Lang.De
    assert config.assistant_speaks_first
    assert not config.tools
    assert config.voice_id is None


async def test_native_events_clear_audio_and_expose_turn_latency_boundary():
    source = AsyncMock()
    source.receive.side_effect = [
        SimpleNamespace(msg_type="audio", interrupted=False, data=b"\xff" * 960),
        SimpleNamespace(msg_type="audio", interrupted=True, data=b"do-not-play"),
        SimpleNamespace(msg_type="event", event=SimpleNamespace(event_type="interrupted")),
        SimpleNamespace(msg_type="event", event=SimpleNamespace(event_type="end_of_turn")),
        None,
    ]
    sink = AsyncMock()
    session = GradbotSession(sink, source)
    assert (await session.receive()).audio == b"\xff" * 960
    assert (await session.receive()).kind == "interrupt"
    assert (await session.receive()).kind == "interrupt"
    assert (await session.receive()).kind == "turn_end"
    assert await session.receive() is None
    await session.close()
    sink.close.assert_awaited_once()


async def test_factory_passes_keys_explicitly_without_mutating_environment(monkeypatch):
    import os

    before = dict(os.environ)
    run = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
    monkeypatch.setattr(gradbot, "run", run)
    session = await GradbotFactory(simulation_settings()).open()
    assert run.call_args.kwargs["llm_api_key"] == "offline"
    assert run.call_args.kwargs["gradium_api_key"] == "offline"
    assert run.call_args.kwargs["input_format"] == gradbot.AudioFormat.Ulaw
    assert run.call_args.kwargs["output_format"] == gradbot.AudioFormat.Ulaw
    assert dict(os.environ) == before
    await asyncio.wait_for(session.close(), 1)
