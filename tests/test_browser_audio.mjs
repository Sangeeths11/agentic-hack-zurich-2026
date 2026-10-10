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
