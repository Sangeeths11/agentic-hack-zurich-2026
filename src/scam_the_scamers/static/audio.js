// Pure signal helpers shared by the UI and deterministic transport tests.
export class LevelTrigger {
  constructor(thresholdDb = -38, holdMs = 180) {
    this.thresholdDb = thresholdDb;
    this.holdMs = holdMs;
    this.aboveMs = 0;
  }
  reset() {
    this.aboveMs = 0;
  }
  update(rms, durationMs) {
    if (20 * Math.log10(Math.max(rms, 1e-8)) >= this.thresholdDb)
      this.aboveMs += durationMs;
    else this.reset();
    return this.aboveMs >= this.holdMs;
  }
}

// Weighted averaging keeps the sample clock continuous across worklet blocks,
// including device rates such as 44.1 kHz. Output is 20ms, mono, little-endian PCM16.
export class PcmFramer {
  constructor(sourceRate, onFrame, targetRate = 24000) {
    this.ratio = sourceRate / targetRate;
    this.remaining = this.ratio;
    this.sum = 0;
    this.energy = 0;
    this.index = 0;
    this.frame = new ArrayBuffer(960);
    this.view = new DataView(this.frame);
    this.onFrame = onFrame;
  }
  push(samples) {
    for (const sample of samples) {
      let available = 1;
      while (available > 1e-9) {
        const weight = Math.min(available, this.remaining);
        this.sum += sample * weight;
        available -= weight;
        this.remaining -= weight;
        if (this.remaining < 1e-9) {
          const value = Math.max(-1, Math.min(1, this.sum / this.ratio));
          this.view.setInt16(
            this.index * 2,
            Math.round(value * (value < 0 ? 32768 : 32767)),
            true,
          );
          this.energy += value * value;
          this.index++;
          this.sum = 0;
          this.remaining = this.ratio;
          if (this.index === 480) {
            this.onFrame(this.frame, Math.sqrt(this.energy / 480));
            this.index = 0;
            this.energy = 0;
            this.frame = new ArrayBuffer(960);
            this.view = new DataView(this.frame);
          }
        }
      }
    }
  }
}

export function decodePcm(data) {
  const view = new DataView(data);
  const samples = new Float32Array(data.byteLength / 2);
  for (let i = 0; i < samples.length; i++)
    samples[i] = view.getInt16(i * 2, true) / 32768;
  return samples;
}
