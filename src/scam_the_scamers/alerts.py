"""Session-local escalation with stable payloads and bounded idempotent retries."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from .caller import CallerLookup
from .config import Settings, normalize_guardian_phone
from .providers import Email, ProviderError, WhatsApp
from .risk import CRITERIA, Assessment

Emit = Callable[[str, str, str], Awaitable[None]]
Notify = Callable[[dict], Awaitable[None]]
SEVERITIES = {50: "Caution", 75: "High", 90: "Critical"}


class AlertManager:
    def __init__(
        self,
        settings: Settings,
        email: Email,
        session_id: str,
        emit: Emit,
        *,
        notify: Notify | None = None,
    ):
        self.settings, self.email, self.session_id, self.emit = settings, email, session_id, emit
        self.notify = notify
        self.highest_attempted = 0
        self.highest_accepted = 0
        self.pending: asyncio.Queue[tuple[int, dict]] = asyncio.Queue(maxsize=1)

    async def consider(self, assessment: Assessment, caller: CallerLookup) -> None:
        level = max(assessment.alert_level, 75 if caller.status == "matched" else 0)
        scores = "; ".join(
            f"{label}: {getattr(assessment.criteria, key):g}/100" for key, label in CRITERIA.items()
        )
        summary = (
            f"Calculated additive score: {assessment.overall:g}/100 (not a probability).\n"
            f"Gemini scam flag: {'yes' if assessment.is_scam else 'no'}.\n{scores}\n"
        )
        await self._consider(level, caller, summary)

    async def consider_caller(self, caller: CallerLookup) -> None:
        if caller.status == "matched":
            await self._consider(
                75,
                caller,
                "Number found in the local suspicious-number database. "
                "The entry may be stale and caller ID may be spoofed.\n"
                "Conversation scores are not yet available.\n",
            )

    async def _consider(self, level: int, caller: CallerLookup, summary: str) -> None:
        if level <= self.highest_attempted:
            return
        self.highest_attempted = level
        if self.settings.email_status() != "configured":
            await self.emit(
                "resend", self.settings.email_status(), "Email alerts are not configured."
            )
            return
        severity = SEVERITIES[level]
        body = {
            "from": self.settings.value("alert_from_email"),
            "to": [self.settings.value("fallback_email")],
            "subject": f"Scam the Scamers: {severity} call warning",
            "text": (
                f"{severity} screening alert at "
                f"{datetime.now(UTC).isoformat(timespec='seconds')}.\n"
                f"{summary}"
                f"Caller supplied by listener: {caller.number or 'not provided'}; "
                f"lookup: {caller.status}.\n"
                "Please check in with the person you support. No transcript is included."
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
                        if self.notify is not None:
                            await self.notify(
                                {
                                    "event": "guardian_alert",
                                    "channel": "email",
                                    "status": "accepted",
                                    "accepted_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                    "level": level,
                                    "severity": SEVERITIES[level],
                                }
                            )
                        await self.emit(
                            "resend",
                            "accepted",
                            f"Tier {level} accepted by Resend. Inbox delivery is unverified.",
                        )
                        break
            finally:
                self.pending.task_done()


def whatsapp_message(
    settings: Settings, level: int, assessment: Assessment | None, caller: CallerLookup
) -> str:
    """Use only known screening evidence; never invent identities or voice matches."""
    heading, description = {
        50: ("⚠️ Scam the Scammer Alert — AMBER", "A call is showing suspicious patterns."),
        75: ("🚨 Scam the Scammer — RED ALERT", "A call has strong warning signs."),
        90: ("🆘 SCAM THE SCAMMER — CRITICAL", "A call has critical warning signs."),
    }[level]
    lines = [heading, description]
    if settings.operator_name.strip() and settings.operator_name != "You":
        lines.append(f"Screening for: {settings.operator_name.strip()}.")
    if assessment is None:
        lines.append("Conversation score: not yet available.")
    else:
        lines.append(f"Score: {assessment.overall:g}/100 (not a fraud probability).")
        if assessment.is_scam:
            lines.append("Gemini independently flagged a suspected scam.")
        # Include the actual evidence scores, without extracting or quoting speech.
        for key, label in CRITERIA.items():
            score = getattr(assessment.criteria, key)
            if score > 0:
                lines.append(f"{'🔴' if score >= 75 else '🔸'} {label}: {score:g}/100")
    if caller.status == "matched":
        lines.append("Caller number is in the local suspicious-number database.")
        lines.append("Listings may be stale and caller ID can be spoofed.")
    lines.append(
        "Please check in with the person you support. Verify the caller independently."
        if level == 50
        else "Contact the person you support NOW. Verify the caller independently."
    )
    lines.append(f"Screening alert at {datetime.now(UTC).isoformat(timespec='seconds')}.")
    return "\n".join(lines)


class WhatsAppAlertManager:
    """Independent, bounded WhatsApp escalation; no unsafe retries of a send GET."""

    def __init__(
        self,
        settings: Settings,
        whatsapp: WhatsApp,
        guardian_phone: str,
        session_id: str,
        emit: Emit,
        *,
        notify: Notify | None = None,
    ):
        self.settings, self.whatsapp, self.session_id = settings, whatsapp, session_id
        self.emit, self.notify = emit, notify
        try:
            self.phone = normalize_guardian_phone(guardian_phone, settings.caller_default_region)
        except ValueError:
            self.phone = "invalid"
        self.highest_attempted = 0
        self.highest_accepted = 0
        self.pending: asyncio.Queue[tuple[int, str]] = asyncio.Queue(maxsize=1)

    async def consider(self, assessment: Assessment, caller: CallerLookup) -> None:
        level = max(assessment.alert_level, 75 if caller.status == "matched" else 0)
        await self._consider(level, assessment, caller)

    async def consider_caller(self, caller: CallerLookup) -> None:
        if caller.status == "matched":
            await self._consider(75, None, caller)

    async def _consider(
        self, level: int, assessment: Assessment | None, caller: CallerLookup
    ) -> None:
        if level <= self.highest_attempted:
            return
        self.highest_attempted = level
        status = self.settings.whatsapp_status(self.phone)
        if status != "configured":
            await self.emit(
                "callmebot", status, "WhatsApp alerts need a valid guardian number and server key."
            )
            return
        body = whatsapp_message(self.settings, level, assessment, caller)
        if self.pending.full():
            self.pending.get_nowait()
            self.pending.task_done()
        self.pending.put_nowait((level, body))

    async def run(self) -> None:
        while True:
            level, body = await self.pending.get()
            try:
                await self.emit("callmebot", "sending", f"Sending tier {level} WhatsApp alert.")
                try:
                    async with asyncio.timeout(self.settings.request_timeout_seconds):
                        await self.whatsapp.send(self.phone, body)
                except (ProviderError, TimeoutError) as exc:
                    uncertain = isinstance(exc, TimeoutError) or exc.code in {
                        "transport_error",
                        "acceptance_unconfirmed",
                        "response_too_large",
                    }
                    await self.emit(
                        "callmebot",
                        "unknown" if uncertain else "error",
                        f"Tier {level} WhatsApp alert not confirmed. No automatic retry; "
                        "check with your guardian.",
                    )
                else:
                    self.highest_accepted = max(level, self.highest_accepted)
                    if self.notify is not None:
                        await self.notify(
                            {
                                "event": "guardian_alert",
                                "channel": "whatsapp",
                                "status": "accepted",
                                "accepted_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                "level": level,
                                "severity": SEVERITIES[level],
                            }
                        )
                    await self.emit(
                        "callmebot",
                        "accepted",
                        "WhatsApp alert queued by CallMeBot. "
                        "Delivery to the guardian is unverified.",
                    )
            finally:
                self.pending.task_done()
