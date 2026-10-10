"""Session-local escalation with stable payloads and bounded idempotent retries."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from .caller import CallerLookup
from .config import Settings, normalize_guardian_phone
from .profiles import EmergencyContact, PersonalProfile, RuntimePreferences
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
            "subject": f"Scam the Scammers: {severity} call warning",
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
    settings: Settings,
    level: int,
    assessment: Assessment | None,
    caller: CallerLookup,
    *,
    profile: PersonalProfile | None = None,
    contact: EmergencyContact | None = None,
) -> str:
    """Personalize from supplied facts; attribute the generated conversation summary."""
    heading, description = {
        50: ("⚠️ Scam the Scammers Alert — AMBER", "A call is showing suspicious patterns."),
        75: ("🚨 Scam the Scammers — RED ALERT", "A call has strong warning signs."),
        90: ("🆘 SCAM THE SCAMMERS — CRITICAL", "A call has critical warning signs."),
    }[level]

    def brief(value: str, limit: int) -> str:
        value = " ".join(value.split())
        return value if len(value) <= limit else value[: limit - 1] + "…"

    lines = [heading]
    if contact and contact.name:
        relation = brief(contact.relationship or "emergency contact", 40)
        lines.append(f"For {brief(contact.name, 50)} ({relation}).")
    listener = profile.listener_name if profile else ""
    if not listener and settings.operator_name.strip() != "You":
        listener = settings.operator_name.strip()
    listener = brief(listener, 60)
    lines.append(f"Call screening for {listener}." if listener else description)
    if assessment is None:
        lines.append("Conversation score: not yet available.")
    else:
        lines.append(f"Score: {assessment.overall:g}/100 (not a fraud probability).")
        if assessment.conversation_summary.strip():
            narrative = brief(assessment.conversation_summary, 420)
            lines.append(f"Conversation summary (AI generated): {narrative}")
        else:
            lines.append("A conversation summary is not available yet.")
        if assessment.is_scam:
            lines.append("Gemini independently flagged a suspected scam.")
        for mismatch in assessment.profile_mismatches[:2]:
            lines.append(f"Profile discrepancy (AI assessment): {brief(mismatch, 120)}")
        evidence = [
            f"{label}: {getattr(assessment.criteria, key):g}/100"
            for key, label in sorted(
                CRITERIA.items(),
                key=lambda item: getattr(assessment.criteria, item[0]),
                reverse=True,
            )[:3]
            if getattr(assessment.criteria, key) > 0
        ]
        if evidence:
            lines.append("Warning signs: " + "; ".join(evidence) + ".")
    if caller.status == "matched":
        lines.append("Caller number is in the local suspicious-number database.")
        lines.append("Listings may be stale and caller ID can be spoofed.")
    lines.append("The alert level reflects the strongest recent finding in this session.")
    who = listener or "the person you support"
    lines.append(
        f"Please check in with {who}. Verify the caller independently."
        if level == 50
        else f"Contact {who} NOW. Verify the caller independently."
    )
    lines.append(f"Screening alert at {datetime.now(UTC).isoformat(timespec='seconds')}.")
    return "\n".join(lines)[:2000]


class WhatsAppAlertManager:
    """Per-recipient escalation with burst coalescing and no ambiguous-send retries."""

    def __init__(
        self,
        settings: Settings,
        whatsapp: WhatsApp,
        guardian_phone: str,
        session_id: str,
        emit: Emit,
        *,
        notify: Notify | None = None,
        preferences: RuntimePreferences | None = None,
    ):
        self.settings, self.whatsapp, self.session_id = settings, whatsapp, session_id
        self.emit, self.notify = emit, notify
        try:
            self.phone = normalize_guardian_phone(guardian_phone, settings.caller_default_region)
        except ValueError:
            self.phone = "invalid"
        self.preferences = preferences
        self.highest_attempted = 0
        self.highest_accepted = 0
        self.attempted: dict[str, int] = {}
        self.last_attempt: dict[str, float] = {}
        self.pending: asyncio.Queue[tuple[int, str]] = asyncio.Queue(maxsize=1)
        self._generation = 0
        self._pending_generation = 0
        self._active_level = 0
        self._active_generation = -1
        self._wake = asyncio.Event()
        self._finishing = False

    def _contacts(self) -> tuple[EmergencyContact, ...]:
        if self.preferences is not None and self.preferences.revision > 0:
            # Explicit empty contacts mean disabled, never silently restore a removed number.
            return self.preferences.profile.emergency_contacts
        if self.phone and self.phone != "invalid":
            return (EmergencyContact(phone=self.phone),)
        return ()

    async def update_preferences(self, preferences: RuntimePreferences) -> None:
        current_revision = self.preferences.revision if self.preferences else 0
        if preferences.revision <= current_revision:
            return
        self.preferences = preferences
        self._generation += 1
        if not self.pending.empty():
            self.pending.get_nowait()
            self.pending.task_done()
        self._wake.set()
        # The session schedules a fresh assessment using the new profile.

    async def finish(self) -> None:
        """Flush an initial burst; discard cooldown-held followups at session end."""
        self._finishing = True
        self._wake.set()

    async def consider(self, assessment: Assessment, caller: CallerLookup) -> None:
        threshold = (
            self.preferences.whatsapp_threshold
            if self.preferences
            else self.settings.whatsapp_threshold
        )
        independent = assessment.is_scam or caller.status == "matched"
        level = (
            max(50, assessment.alert_level, 75 if independent else 0)
            if assessment.overall >= threshold or independent
            else 0
        )
        await self._consider(level, assessment, caller)

    async def consider_caller(self, caller: CallerLookup) -> None:
        if caller.status == "matched":
            await self._consider(75, None, caller)

    async def _consider(
        self, level: int, assessment: Assessment | None, caller: CallerLookup
    ) -> None:
        if self._finishing:
            return
        if self._active_generation == self._generation:
            level = max(level, self._active_level)
        if not self.pending.empty():
            old_level, _ = self.pending.get_nowait()
            self.pending.task_done()
            level = max(level, old_level)
        if not level:
            return
        contacts = self._contacts()
        available = [
            person
            for person in contacts
            if level > self.attempted.get(person.phone, 0)
            and self.settings.whatsapp_status(person.phone) == "configured"
        ]
        if not available:
            if not contacts:
                status = (
                    "invalid_configuration"
                    if self.phone == "invalid"
                    and (self.preferences is None or self.preferences.revision == 0)
                    else "not_configured"
                )
                await self.emit(
                    "callmebot",
                    status,
                    "Add an emergency contact with a matching server key "
                    "to enable WhatsApp alerts.",
                )
            elif not any(
                self.settings.whatsapp_status(person.phone) == "configured" for person in contacts
            ):
                status = self.settings.whatsapp_status(contacts[0].phone)
                await self.emit(
                    "callmebot",
                    status,
                    "Emergency contacts need matching activated CallMeBot keys on the server.",
                )
            return
        profile = self.preferences.profile if self.preferences else None
        body = whatsapp_message(self.settings, level, assessment, caller, profile=profile)
        self._pending_generation = self._generation
        self.pending.put_nowait((level, body))
        self._wake.set()

    def _take_latest(self, level: int, body: str, generation: int) -> tuple[int, str, int]:
        if not self.pending.empty():
            level, body = self.pending.get_nowait()
            generation = self._pending_generation
            # The new queue item replaces the held item, leaving one unfinished task.
            self.pending.task_done()
        self._active_level, self._active_generation = level, generation
        return level, body, generation

    async def run(self) -> None:
        while True:
            level, body = await self.pending.get()
            generation = self._pending_generation
            self._active_level, self._active_generation = level, generation
            started = time.monotonic()
            debounce = self.settings.whatsapp_debounce_seconds
            deadline, burst_limit = started + debounce, started + debounce * 2
            try:
                while True:
                    self._wake.clear()
                    if not self.pending.empty():
                        level, body, generation = self._take_latest(level, body, generation)
                        deadline = min(time.monotonic() + debounce, burst_limit)
                    if generation != self._generation:
                        break
                    contacts = [
                        person
                        for person in self._contacts()
                        if level > self.attempted.get(person.phone, 0)
                        and self.settings.whatsapp_status(person.phone) == "configured"
                    ]
                    if not contacts:
                        break
                    now = time.monotonic()
                    cooldown = (
                        self.preferences.whatsapp_cooldown_seconds
                        if self.preferences
                        else self.settings.whatsapp_cooldown_seconds
                    )
                    ready = [
                        person
                        for person in contacts
                        if now >= self.last_attempt.get(person.phone, -float("inf")) + cooldown
                    ]
                    if self._finishing and not ready:
                        await self.emit(
                            "callmebot",
                            "complete",
                            "Session ended; pending repeated alerts were discarded.",
                        )
                        break
                    delay = max(0.0, deadline - now) if not self._finishing else 0.0
                    if not ready:
                        delay = max(
                            delay,
                            min(
                                self.last_attempt[person.phone] + cooldown - now
                                for person in contacts
                            ),
                        )
                    if delay:
                        await self.emit(
                            "callmebot",
                            "coalescing",
                            "Combining recent findings into one WhatsApp alert.",
                        )
                        try:
                            await asyncio.wait_for(self._wake.wait(), delay)
                        except TimeoutError:
                            pass
                        continue
                    for contact in ready:
                        if generation != self._generation:
                            break
                        personalized_body = body
                        if contact.name:
                            relation = " ".join(
                                (contact.relationship or "emergency contact").split()
                            )[:40]
                            name = " ".join(contact.name.split())[:50]
                            personalized_body = f"For {name} ({relation}).\n{body}"[:2000]
                        await self._send(contact, level, personalized_body, generation)
                    if all(level <= self.attempted.get(person.phone, 0) for person in contacts):
                        break
            finally:
                self._active_level, self._active_generation = 0, -1
                self.pending.task_done()

    async def _send(
        self, contact: EmergencyContact, level: int, body: str, generation: int
    ) -> None:
        await self.emit("callmebot", "sending", f"Sending tier {level} WhatsApp alert.")
        # The event queue may block while settings change. Recheck immediately before
        # the irreversible provider request, not just before announcing its activity.
        if generation != self._generation or contact not in self._contacts():
            return
        self.attempted[contact.phone] = level
        self.highest_attempted = max(self.highest_attempted, level)
        self.last_attempt[contact.phone] = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                await self.whatsapp.send(contact.phone, body)
        except (ProviderError, TimeoutError) as exc:
            uncertain = isinstance(exc, TimeoutError) or exc.code in {
                "transport_error",
                "acceptance_unconfirmed",
                "response_too_large",
            }
            await self.emit(
                "callmebot",
                "unknown" if uncertain else "error",
                f"Tier {level} WhatsApp alert not confirmed. "
                "No automatic retry; check with your contact.",
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
                        "contact_phone": contact.phone,
                        "contact_name": contact.name,
                        "contact_relationship": contact.relationship,
                    }
                )
            await self.emit(
                "callmebot",
                "accepted",
                "WhatsApp alert queued by CallMeBot. Delivery to the contact is unverified.",
            )
