"""Streaming μ-law conversion. Rates are part of the pinned Gradbot 0.1.9 ABI."""

import audioop

TWILIO_RATE = 8_000
GRADBOT_INPUT_RATE = 24_000
GRADBOT_OUTPUT_RATE = 48_000


class UlawResampler:
    def __init__(self, source_rate: int, target_rate: int):
        self.source_rate = source_rate
        self.target_rate = target_rate
        self._state: tuple[int, tuple[tuple[int, int], ...]] | None = None

    def reset(self) -> None:
        self._state = None

    def convert(self, data: bytes) -> bytes:
        if not data or self.source_rate == self.target_rate:
            return data
        pcm = audioop.ulaw2lin(data, 2)
        converted, self._state = audioop.ratecv(
            pcm, 2, 1, self.source_rate, self.target_rate, self._state
        )
        return audioop.lin2ulaw(converted, 2)
