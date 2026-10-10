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
    event.criteria && Object.keys(event.criteria).length === 6 &&
    Object.keys(criteria).every((key) => riskColor(event.criteria[key]) !== "unavailable");
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
