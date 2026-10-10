from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest
from support import simulation_settings

from scam_the_scamers import tts


class _FakeClient:
    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.tts = AsyncMock(side_effect=self._call)

    async def _call(self, setup, text):
        if self._raises is not None:
            raise self._raises
        return self._result


def _fake_result(samples: list[int], output_format="pcm", sample_rate=48_000):
    array = np.array(samples, dtype=np.int16)
    return SimpleNamespace(
        output_format=output_format, sample_rate=sample_rate, pcm16=lambda: array
    )


async def test_render_pcm16_returns_raw_bytes_from_gradium(monkeypatch):
    client = _FakeClient(result=_fake_result([1, -1, 2, -2]))
    monkeypatch.setattr(tts.gradium, "GradiumClient", lambda **kwargs: client)
    settings = simulation_settings()
    audio = await tts.render_pcm16(settings, "This call shows some risk signs.")
    assert audio == np.array([1, -1, 2, -2], dtype=np.int16).tobytes()


async def test_render_pcm16_passes_the_text_through_unchanged(monkeypatch):
    client = _FakeClient(result=_fake_result([0]))
    monkeypatch.setattr(tts.gradium, "GradiumClient", lambda **kwargs: client)
    settings = simulation_settings()
    await tts.render_pcm16(settings, "Please take a moment to verify this.")
    assert client.tts.call_args.args[1] == "Please take a moment to verify this."


async def test_render_pcm16_rejects_unexpected_format(monkeypatch):
    client = _FakeClient(result=_fake_result([1], output_format="wav", sample_rate=48_000))
    monkeypatch.setattr(tts.gradium, "GradiumClient", lambda **kwargs: client)
    settings = simulation_settings()
    assert await tts.render_pcm16(settings, "text") is None


async def test_render_pcm16_rejects_unexpected_sample_rate(monkeypatch):
    client = _FakeClient(result=_fake_result([1], sample_rate=24_000))
    monkeypatch.setattr(tts.gradium, "GradiumClient", lambda **kwargs: client)
    settings = simulation_settings()
    assert await tts.render_pcm16(settings, "text") is None


async def test_render_pcm16_degrades_on_provider_error(monkeypatch):
    client = _FakeClient(raises=RuntimeError("boom"))
    monkeypatch.setattr(tts.gradium, "GradiumClient", lambda **kwargs: client)
    settings = simulation_settings()
    assert await tts.render_pcm16(settings, "text") is None


async def test_render_pcm16_skips_without_a_gradium_key():
    settings = simulation_settings(gradium_api_key="")
    assert await tts.render_pcm16(settings, "text") is None


@pytest.mark.parametrize("text", ["", "   "])
async def test_render_pcm16_short_circuits_on_empty_text(text):
    settings = simulation_settings()
    assert await tts.render_pcm16(settings, text) is None
