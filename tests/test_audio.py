import audioop
import struct

from familyshield.audio import UlawResampler
from familyshield.simulation import tone


def test_resampling_preserves_duration_pitch_and_chunk_boundaries():
    original = tone(8000, frequency=440, seconds=1)
    up = UlawResampler(8000, 24000)
    chunks = [up.convert(original[i : i + 160]) for i in range(0, len(original), 160)]
    converted = b"".join(chunks)
    assert converted == UlawResampler(8000, 24000).convert(original)
    assert abs(len(converted) / 24000 - 1) < 0.001
    down = UlawResampler(48000, 8000)
    output = down.convert(tone(48000, frequency=440, seconds=1))
    assert len(output) == 8000
    pcm = audioop.ulaw2lin(output, 2)
    samples = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
    crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:], strict=False))
    assert abs(crossings - 440) <= 1  # a sample-rate error shifts the audible pitch


def test_reset_removes_previous_turn_resampler_state():
    converter = UlawResampler(48000, 8000)
    converter.convert(tone(48000))
    converter.reset()
    data = tone(48000, frequency=660)
    assert converter.convert(data) == UlawResampler(48000, 8000).convert(data)
