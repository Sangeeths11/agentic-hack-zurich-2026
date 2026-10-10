import { PcmFramer } from "./audio.js";

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.framer = new PcmFramer(sampleRate, (pcm, rms) => {
      this.port.postMessage({ pcm, rms }, [pcm]);
    });
  }
  process(inputs) {
    if (inputs[0]?.[0]) this.framer.push(inputs[0][0]);
    // Output stays silent: never route the raw microphone to the speakers.
    return true;
  }
}
registerProcessor("listener-capture", CaptureProcessor);
