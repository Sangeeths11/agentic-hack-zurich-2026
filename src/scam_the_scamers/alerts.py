"""Session-local escalation with stable payloads and bounded idempotent retries."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from .caller import CallerLookup
from .config import Settings
from .providers import Email, ProviderError
from .risk import CRITERIA, Assessment, tier

Emit = Callable[[str, str, str], Awaitable[None]]


class AlertManager:
    def __init__(self, settings: Settings, email: Email, session_id: str, emit: Emit):
        self.settings, self.email, self.session_id, self.emit = settings, email, session_id, emit
        self.highest_attempted = 0
        self.highest_accepted = 0
        self.pending: asyncio.Queue[tuple[int, dict]] = asyncio.Queue(maxsize=1)

    async def consider(self, assessment: Assessment, caller: CallerLookup) -> None:
        level = tier(assessment.overall)
        if level <= self.highest_attempted:
            return
        self.highest_attempted = level
        if self.settings.email_status() != "configured":
            await self.emit(
                "resend", self.settings.email_status(), "Email alerts are not configured."
            )
            return
        severity = {50: "Caution", 75: "High", 90: "Critical"}[level]
        scores = "; ".join(
            f"{label}: {getattr(assessment.criteria, key):g}/100" for key, label in CRITERIA.items()
        )
        body = {
            "from": self.settings.value("alert_from_email"),
            "to": [self.settings.value("fallback_email")],
            "subject": f"Scam the Scamers: {severity} call warning",
            "text": (
                f"{severity} screening alert at "
                f"{datetime.now(UTC).isoformat(timespec='seconds')}.\n"
                f"Risk indicator: {assessment.overall:g}/100 (not a probability).\n"
                f"Caller supplied by listener: {caller.number or 'not provided'}; "
                f"lookup: {caller.status}.\n"
                f"{scores}\nPlease check in with the person you support. No transcript is included."
            ),
        }
        # Coalesce unsent escalations. A direct jump creates only its highest tier.
        if self.pending.full():
            self.pending.get_nowait()
            self.pending.task_done()
        self.pending.put_nowait((level, body))

    async def run(self) -> None:
        while True:
            level, body = await self.pending.get()
            key = f"scam-screening/{self.session_id}/{level}"
            try:
                for attempt in range(2):
                    await self.emit(
                        "resend", "sending", f"Sending tier {level} alert (attempt {attempt + 1})."
                    )
                    try:
                        await self.email.send(body, key)
                    except (ProviderError, TimeoutError) as exc:
                        retryable = isinstance(exc, TimeoutError) or exc.retryable
                        if attempt == 0 and retryable and self.pending.empty():
                            await self.emit(
                                "resend",
                                "retrying",
                                "Temporary email failure; one idempotent retry.",
                            )
                            await asyncio.sleep(1)
                            if self.pending.empty():
                                continue
                        await self.emit(
                            "resend",
                            "error",
                            f"Tier {level} email not confirmed; no further automatic retry.",
                        )
                        break
                    else:
                        self.highest_accepted = max(level, self.highest_accepted)
                        await self.emit(
                            "resend",
                            "accepted",
                            f"Tier {level} accepted by Resend. Inbox delivery is unverified.",
                        )
                        break
            finally:
                self.pending.task_done()
