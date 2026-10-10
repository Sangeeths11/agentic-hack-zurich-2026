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
    if (event.level <= (this.accepted.get(channel)?.level ?? 0)) return false;
    this.accepted.set(channel, { ...event, channel });
    return true;
  }
  latest() {
    return [...this.accepted.values()].sort((a, b) => b.level - a.level || Date.parse(b.accepted_at) - Date.parse(a.accepted_at))[0];
  }
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
