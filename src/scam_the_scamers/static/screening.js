export const criteria = {
  family_claim: "Family-member claim",
  emotional_distress: "Emotional distress",
  urgency: "Urgency",
  secrecy: "Secrecy",
  financial_request: "Financial request",
  courier_pickup: "Courier pickup",
};
export function riskColor(score) {
  if (!Number.isFinite(score) || score < 0 || score > 100) return "unavailable";
  return score >= 90 ? "critical" : score >= 75 ? "red" : score >= 50 ? "amber" : "green";
}
export function validAssessment(event) {
  return Number.isInteger(event.revision) && event.revision > 0 &&
    riskColor(event.overall) !== "unavailable" &&
    typeof event.is_scam === "boolean" && [0, 50, 75, 90].includes(event.alert_level) &&
    event.criteria && Object.keys(event.criteria).length === 6 &&
    Object.keys(criteria).every((key) => riskColor(event.criteria[key]) !== "unavailable");
}
export function assessmentColor(event, matched = false) {
  const level = Math.max(event?.overall ?? 0, event?.alert_level ?? 0, event?.is_scam ? 75 : 0, matched ? 75 : 0);
  return !event && !matched ? "unavailable" : riskColor(level);
}
export function validGuardianAlert(event) {
  return event.status === "accepted" &&
    [undefined, "email", "whatsapp"].includes(event.channel) &&
    [50, 75, 90].includes(event.level) &&
    event.severity === { 50: "Caution", 75: "High", 90: "Critical" }[event.level] &&
    typeof event.accepted_at === "string" && Number.isFinite(Date.parse(event.accepted_at));
}
export class GuardianAlerts {
  constructor() { this.accepted = new Map(); }
  accept(event) {
    if (!validGuardianAlert(event)) return false;
    const channel = event.channel ?? "email";
    const key = channel === "whatsapp" && typeof event.contact_phone === "string" ? `${channel}:${event.contact_phone}` : channel;
    if (event.level <= (this.accepted.get(key)?.level ?? 0)) return false;
    this.accepted.set(key, { ...event, channel });
    return true;
  }
  latest() {
    return [...this.accepted.values()].sort((a, b) => b.level - a.level || Date.parse(b.accepted_at) - Date.parse(a.accepted_at))[0];
  }
}
export function displayTranscriptTurns(turns) {
  if (!Array.isArray(turns) || !turns.length || turns.length > 400) return null;
  const valid = turns.every((turn) => turn && Number.isSafeInteger(turn.id) && turn.id > 0 && typeof turn.text === "string" && ["caller", "listener", "unknown"].includes(turn.role));
  if (!valid || turns.reduce((total, turn) => total + turn.text.length, 0) > 24000) return null;
  if (new Set(turns.map((turn) => turn.id)).size !== turns.length) return null;
  return turns.map((turn) => ({
    id: turn.id,
    text: turn.text,
    role: turn.role,
    ...(Number.isFinite(turn.start_s) && turn.start_s >= 0 && turn.start_s <= 3600 ? { start_s: turn.start_s } : {}),
  }));
}
export class PlaybackGuard {
  constructor() { this.until = 0; }
  begin(now, seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0 || seconds > 12) return false;
    this.until = now + seconds * 1000 + 1000;
    return true;
  }
  active(now) { return now < this.until; }
  cancel(now) { this.until = now + 500; }
  reset() { this.until = 0; }
}
