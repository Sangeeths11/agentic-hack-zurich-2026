import assert from "node:assert/strict";
import test from "node:test";
import {
  LevelTrigger,
  PcmFramer,
  decodePcm,
} from "../src/scam_the_scamers/static/audio.js";

test("sustained speech triggers after 180ms; silence and isolated clicks do not", () => {
  const trigger = new LevelTrigger(-38);
  for (let i = 0; i < 100; i++) assert.equal(trigger.update(0, 20), false);
  assert.equal(trigger.update(0.5, 20), false);
  assert.equal(trigger.update(0, 20), false);
  for (let i = 0; i < 8; i++) assert.equal(trigger.update(0.1, 20), false);
  assert.equal(trigger.update(0.1, 20), true);
  trigger.reset();
  assert.equal(trigger.update(0.1, 20), false);
  trigger.thresholdDb = -20;
  for (let i = 0; i < 20; i++) assert.equal(trigger.update(0.02, 20), false);
});

for (const rate of [24000, 44100, 48000, 96000]) {
  test(`continuous 24kHz PCM at ${rate}Hz input preserves timing, pitch and block boundaries`, () => {
    const input = Float32Array.from(
      { length: rate },
      (_, i) => 0.5 * Math.sin((2 * Math.PI * 440 * i) / rate),
    );
    const whole = [],
      split = [];
    new PcmFramer(rate, (frame) => whole.push(Buffer.from(frame))).push(input);
    const chunked = new PcmFramer(rate, (frame, rms) => {
      assert.ok(rms > 0.3 && rms < 0.4);
      split.push(Buffer.from(frame));
    });
    for (let i = 0; i < input.length; i += 128)
      chunked.push(input.slice(i, i + 128));
    assert.equal(split.length, 50);
    assert.deepEqual(Buffer.concat(split), Buffer.concat(whole));
    const output = Buffer.concat(split);
    const decoded = decodePcm(
      output.buffer.slice(
        output.byteOffset,
        output.byteOffset + output.byteLength,
      ),
    );
    assert.equal(decoded.length, 24000);
    let crossings = 0;
    for (let i = 1; i < decoded.length; i++)
      if (decoded[i - 1] <= 0 && decoded[i] > 0) crossings++;
    assert.ok(Math.abs(crossings - 440) <= 1);
  });
}

test("PCM clipping and signed little-endian decoding", () => {
  let output;
  new PcmFramer(24000, (frame) => {
    output = frame;
  }).push(Float32Array.from({ length: 480 }, (_, i) => (i % 2 ? -2 : 2)));
  const decoded = decodePcm(output);
  assert.equal(decoded[0], 32767 / 32768);
  assert.equal(decoded[1], -1);
});

const { PlaybackGuard, riskColor, validAssessment, assessmentColor, validGuardianAlert, GuardianAlerts, criteria } = await import("../src/scam_the_scamers/static/screening.js");
for (const [score, color] of [[49,"green"],[50,"amber"],[74,"amber"],[75,"red"],[89,"red"],[90,"critical"],[NaN,"unavailable"],[-1,"unavailable"]]) {
  test(`risk boundary ${score}: ${color}`, () => assert.equal(riskColor(score), color));
}
test("playback guard is bounded, cancels with a tail, and resumes", () => {
  const guard = new PlaybackGuard();
  assert.equal(guard.begin(0, 13), false);
  assert.equal(guard.begin(100, 2), true);
  assert.equal(guard.active(3099), true);
  assert.equal(guard.active(3100), false);
  guard.begin(4000, 2); guard.cancel(4100);
  assert.equal(guard.active(4599), true);
  assert.equal(guard.active(4600), false);
  guard.reset(); assert.equal(guard.active(0), false);
});
test("assessment contract requires every criterion and finite values", () => {
  const value = { revision: 1, overall: 50, is_scam: false, alert_level: 50, criteria: Object.fromEntries(Object.keys(criteria).map(k => [k, 50])) };
  assert.equal(validAssessment(value), true);
  value.criteria.secrecy = NaN; assert.equal(validAssessment(value), false);
  delete value.criteria.secrecy; assert.equal(validAssessment(value), false);
});
test("independent scam and listed-number flags remain visible below score thresholds", () => {
  assert.equal(assessmentColor(null), "unavailable");
  assert.equal(assessmentColor(null, true), "red");
  assert.equal(assessmentColor({ overall: 10, alert_level: 0, is_scam: false }), "green");
  assert.equal(assessmentColor({ overall: 10, alert_level: 75, is_scam: true }), "red");
  assert.equal(assessmentColor({ overall: 90, alert_level: 90, is_scam: false }, true), "critical");
});
test("guardian notification requires a timestamped provider acceptance with valid severity", () => {
  const event = { status: "accepted", level: 75, severity: "High", accepted_at: "2026-10-10T12:30:00Z" };
  assert.equal(validGuardianAlert(event), true);
  assert.equal(validGuardianAlert({ ...event, status: "sending" }), false);
  assert.equal(validGuardianAlert({ ...event, severity: "Critical" }), false);
  assert.equal(validGuardianAlert({ ...event, accepted_at: "bad" }), false);
  assert.equal(validGuardianAlert({ ...event, channel: "whatsapp" }), true);
  assert.equal(validGuardianAlert({ ...event, channel: "email" }), true);
  assert.equal(validGuardianAlert({ ...event, channel: "sms" }), false);
  assert.equal(validGuardianAlert({ ...event, channel: "whatsapp", status: "unknown" }), false);
});
test("guardian acceptance is tracked independently per channel without downgrading the banner", () => {
  const alerts = new GuardianAlerts();
  const event = { status: "accepted", level: 75, severity: "High", accepted_at: "2026-10-10T12:30:00Z" };
  assert.equal(alerts.accept(event), true); // Legacy email event.
  assert.equal(alerts.accept({ ...event, channel: "whatsapp", status: "sending" }), false);
  assert.equal(alerts.accept({ ...event, channel: "whatsapp" }), true);
  assert.equal(alerts.accepted.size, 2);
  assert.equal(alerts.accept({ ...event, channel: "email" }), false);
  assert.equal(alerts.accept({ ...event, channel: "whatsapp" }), false);
  assert.equal(alerts.accept({ ...event, channel: "whatsapp", level: 90, severity: "Critical" }), true);
  assert.equal(alerts.accept({ ...event, level: 50, severity: "Caution" }), false);
  assert.equal(alerts.latest().severity, "Critical");
  assert.equal(alerts.latest().channel, "whatsapp");
  assert.equal(new GuardianAlerts().accepted.size, 0);
});
