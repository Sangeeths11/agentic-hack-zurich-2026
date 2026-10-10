"""Provider boundary; transport never depends on native SDK message types."""

import json
from dataclasses import dataclass
from typing import Literal, Protocol

import gradbot

from .config import Settings


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["audio", "interrupt", "turn_end", "activity"]
    audio: bytes = b""


class AgentSession(Protocol):
    async def send_audio(self, audio: bytes) -> None: ...
    async def receive(self) -> AgentEvent | None: ...
    async def close(self) -> None: ...


class AgentFactory(Protocol):
    async def open(self) -> AgentSession: ...


def session_config(settings: Settings) -> gradbot.SessionConfig:
    languages = {
        "de": "German",
        "en": "English",
        "fr": "French",
        "es": "Spanish",
        "pt": "Portuguese",
    }
    instructions = (
        f"You are the AI assistant for {settings.operator_name}, running on a laptop "
        "next to a phone on speaker. You hear the conversation through the laptop microphone "
        "and your voice plays through the laptop speakers. "
        f"Speak {languages[settings.agent_language]}. Speak first: briefly greet the caller, "
        "explicitly introduce yourself as an AI assistant acting for the owner, "
        "and ask how you can help. Use one or two short natural sentences at a time. "
        "Ask one question at a time. Listen when interrupted. "
        "Help with simple questions and clarify why the caller called. "
        "You have no access to the owner's calendar, messages or private information. "
        "Do not invent facts, make commitments or claim to save or deliver messages. "
        "You cannot detect, answer, transfer or hang up a phone call. "
        "If asked, explain that this is a conversation prototype. Never claim to be the owner."
    )
    return gradbot.SessionConfig(
        voice_id=settings.gradium_voice_id or None,
        instructions=instructions,
        language=getattr(gradbot.Lang, settings.agent_language.capitalize()),
        assistant_speaks_first=True,
        silence_timeout_s=settings.silence_timeout_seconds,
        flush_duration_s=0.5,
        stt_extra_config=json.dumps({"padding_bonus": 1.0}),
        tools=[],
    )


class GradbotSession:
    def __init__(self, input_handle, output_handle):
        self._input = input_handle
        self._output = output_handle

    async def send_audio(self, audio: bytes) -> None:
        await self._input.send_audio(audio)

    async def receive(self) -> AgentEvent | None:
        msg = await self._output.receive()
        if msg is None:
            return None
        if msg.msg_type == "audio":
            if msg.interrupted:
                return AgentEvent("interrupt")
            return AgentEvent("audio", bytes(msg.data))
        if msg.msg_type == "event" and msg.event.event_type == "interrupted":
            return AgentEvent("interrupt")
        if msg.msg_type == "event" and msg.event.event_type == "end_of_turn":
            return AgentEvent("turn_end")
        # Transcript contents deliberately never enter the transport logs.
        return AgentEvent("activity")

    async def close(self) -> None:
        await self._input.close()


class GradbotFactory:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def open(self) -> AgentSession:
        settings = self.settings
        input_handle, output_handle = await gradbot.run(
            gradium_api_key=settings.value("gradium_api_key"),
            gradium_base_url=settings.gradium_base_url or None,
            llm_api_key=settings.value("llm_api_key"),
            llm_base_url=settings.llm_base_url,
            llm_model_name=settings.llm_model,
            max_completion_tokens=180,
            session_config=session_config(settings),
            input_format=gradbot.AudioFormat.Ulaw,
            output_format=gradbot.AudioFormat.Ulaw,
        )
        return GradbotSession(input_handle, output_handle)
