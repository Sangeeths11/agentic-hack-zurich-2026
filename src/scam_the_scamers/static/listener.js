import { LevelTrigger, decodePcm } from "./audio.js";
import { addActivity, showActivityPhase, showTool } from "./activity.js";

import { criteria, riskColor, validAssessment, PlaybackGuard } from "./screening.js";

const guard = new PlaybackGuard();
let revision = 0, finishing = false, warningPlaybackAllowed = false;
const $ = (id) => document.getElementById(id);
for (const [key, label] of Object.entries(criteria)) {
  const row = document.createElement("div"); row.className = "criterion"; row.id = `criterion-${key}`; row.dataset.risk = "unavailable";
  const name = document.createElement("span"); name.textContent = label;
  const value = document.createElement("strong"); value.id = `score-${key}`; value.textContent = "—";
  row.append(name, value); $("criteria-scores").append(row);
}
const trigger = new LevelTrigger();
let enabled = false,
  preparing = false,
  stopping = false,
  generation = 0;
let context, microphone, capture, source, socket, startupTimer;
let phase = "off",
  preRoll = [],
  pending = [],
  sources = new Set();
let playbackUntil = 0,
  lastActivity = 0,
  startedAt = 0,
  cooldownUntil = 0;
let configured = false,
  maxSessionSeconds = 300;
const states = {
  off: [
    "READY TO LISTEN",
    "Switch it on. Then just speak.",
    "Enable your microphone to get the listener ready.",
  ],
  armed: [
    "LISTENER ACTIVE",
    "I'm listening.",
    "Screening starts automatically when someone speaks.",
  ],
  connecting: [
    "VOICE DETECTED",
    "Connecting screening…",
    "Gradium STT is connecting. The start of your sentence is briefly buffered.",
  ],
  listening: [
    "SCREENING ACTIVE",
    "I'm listening.",
    "Scores update quietly. Only eligible cautions play through your laptop.",
  ],
  thinking: [
    "SCREENING ACTIVE",
    "One moment …",
    "Gemini is assessing the accumulated call context.",
  ],
  speaking: [
    "SCREENING ACTIVE",
    "A moment of caution.",
    "Microphone forwarding resumes after the warning. You can stop the audio.",
  ],
  error: [
    "LISTENER PAUSED",
    "Something needs attention.",
    "Follow the instructions below, then enable the listener again.",
  ],
};
function setPhase(next) {
  if (phase === next && $("orb").dataset.state === next) return;
  const previous = phase;
  phase = next;
  showActivityPhase(next, previous);
  $("orb").dataset.state = next;
  const [label, title, detail] = states[next];
  $("mode-label").textContent = label;
  $("status-title").textContent = title;
  $("status-detail").textContent = detail;
  $("end-session").hidden = ![
    "connecting",
    "listening",
    "thinking",
    "speaking",
  ].includes(next);
}
function clearPlayback() {
  for (const node of sources) {
    node.onended = null;
    try {
      node.stop();
    } catch {}
    node.disconnect();
  }
  sources.clear();
  playbackUntil = 0;
}
function closeSession(
  rearm = true,
  reason = "The screening connection has ended.",
) {
  clearTimeout(startupTimer);
  finishing = false;
  warningPlaybackAllowed = false;
  guard.reset();
  $("cancel-warning").hidden = true;
  const old = socket;
  socket = null;
  if (old) {
    addActivity("Screening ended", reason);
    if (old.readyState === WebSocket.OPEN)
      old.send(JSON.stringify({ event: "stop" }));
    old.close();
  }
  clearPlayback();
  pending = [];
  preRoll = [];
  trigger.reset();
  cooldownUntil = performance.now() + 1000;
  $("session-time").textContent = "LOCAL / 01";
  if (old) {
    $("assessment-status").textContent = "Session ended. Last assessment is no longer live.";
    for (const tool of ["stt", "gemini", "tts"]) $(`tool-${tool}`).textContent = "Disconnected";
  }
  if (rearm && enabled) setPhase("armed");
}
async function stop() {
  if (stopping) return;
  stopping = true;
  $("toggle").disabled = true;
  enabled = false;
  preparing = false;
  generation++;
  closeSession(false, "The listener was paused.");
  if (capture) {
    capture.port.onmessage = null;
    capture.disconnect();
    capture = null;
  }
  if (source) {
    source.disconnect();
    source = null;
  }
  if (microphone) {
    microphone.getTracks().forEach((track) => track.stop());
    microphone = null;
  }
  const oldContext = context;
  context = null;
  if (oldContext) {
    oldContext.onstatechange = null;
    await oldContext.close().catch(() => {});
  }
  document.documentElement.style.setProperty("--level", 0);
  $("level-value").textContent = "— dB";
  $("caller-number").disabled = false;
  $("toggle-label").textContent = "Enable listener";
  $("toggle").disabled = !configured;
  stopping = false;
  setPhase("off");
}
async function fail(message) {
  await stop();
  resetScores("Screening unavailable. Enable the listener again after resolving the error.");
  $("error").textContent = message;
  $("error").hidden = false;
  setPhase("error");
  addActivity("Something needs attention", message, "error");
}
function pump() {
  if (!socket || socket.readyState !== WebSocket.OPEN || phase === "connecting")
    return;
  if (socket.bufferedAmount > 96000) {
    void fail(
      "The audio connection is too slow. Please enable the listener again.",
    );
    return;
  }
  // Catch up the bounded startup buffer gradually, preserving order without flooding the server.
  for (let i = 0; i < 4 && pending.length; i++) socket.send(pending.shift());
}
function play(data) {
  if (!warningPlaybackAllowed) return;
  if (!context || context.state !== "running") {
    void fail("Warning playback is blocked. Enable browser audio and try again.");
    return;
  }
  if (!data.byteLength || data.byteLength % 2) {
    void fail("Invalid response audio. Please enable the listener again.");
    return;
  }
  if (playbackUntil - context.currentTime > 12) {
    void fail(
      "Audio playback has fallen too far behind. Please enable the listener again.",
    );
    return;
  }
  const buffer = context.createBuffer(1, data.byteLength / 2, 48000);
  buffer.copyToChannel(decodePcm(data), 0);
  const node = context.createBufferSource();
  node.buffer = buffer;
  node.connect(context.destination);
  const at = Math.max(context.currentTime + 0.025, playbackUntil);
  node.start(at);
  playbackUntil = at + buffer.duration;
  sources.add(node);
  node.onended = () => {
    sources.delete(node);
    node.disconnect();
  };
  lastActivity = performance.now();
  setPhase("speaking");
}
function startSession() {
  if (!enabled || socket) return;
  trigger.reset();
  revision = 0;
  resetScores("Waiting for speech and a valid assessment.");
  $("warning-text").hidden = true;
  $("tool-tts").textContent = "No warning requested";
  setPhase("connecting");
  pending = preRoll.slice();
  preRoll = [];
  const ws = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/audio/stream`,
  );
  socket = ws;
  ws.binaryType = "arraybuffer";
  startedAt = performance.now();
  lastActivity = startedAt;
  startupTimer = setTimeout(() => {
    if (socket === ws)
      void fail(
        "The assistant did not start in time. Check the server and API credentials.",
      );
  }, 18000);
  ws.onopen = () => {
    if (socket === ws)
      ws.send(
        JSON.stringify({
          event: "start",
          encoding: "pcm_s16le",
          sample_rate: 24000,
          channels: 1,
          caller_number: $("caller-number").value,
        }),
      );
  };
  ws.onmessage = ({ data }) => {
    if (socket !== ws || !enabled) return;
    if (data instanceof ArrayBuffer) {
      play(data);
      return;
    }
    let event;
    try {
      event = JSON.parse(data);
    } catch {
      void fail("Invalid server response.");
      return;
    }
    if (event.event === "ready") {
      if (event.input_rate !== 24000 || event.output_rate !== 48000) {
        void fail("The audio format is not compatible with the listener.");
        return;
      }
      clearTimeout(startupTimer);
      showCaller(event.caller);
      $("tool-resend").textContent = event.email_status.replaceAll("_", " ");
      setPhase("listening");
      pump();
    } else if (event.event === "assessment") {
      if (!validAssessment(event)) { void fail("Invalid assessment from server."); return; }
      if (event.revision <= revision) return;
      revision = event.revision;
      showScores(event);
      if (phase === "thinking") setPhase("listening");
    } else if (event.event === "assessment_unavailable") {
      if (event.revision < revision) return;
      revision = event.revision;
      resetScores("Assessment unavailable. Listening continues; waiting for new speech.");
      if (phase === "thinking") setPhase("listening");
    } else if (event.event === "tool") {
      showTool(event);
      if (event.tool === "gemini" && event.status === "assessing" && phase === "listening") setPhase("thinking");
    } else if (event.event === "warning") {
      if (!guard.begin(performance.now(), event.duration_seconds)) { void fail("Invalid warning audio duration."); return; }
      warningPlaybackAllowed = true;
      $("warning-text").textContent = event.text;
      $("warning-text").hidden = false;
      $("cancel-warning").hidden = false;
    } else if (event.event === "clear") {
      warningPlaybackAllowed = false;
      clearPlayback();
      guard.cancel(performance.now());
      $("cancel-warning").hidden = true;
      setPhase("listening");
    }

  };
  ws.onclose = (event) => {
    if (socket !== ws) return;
    if (event.code === 1000) closeSession();
    else
      void fail(
        event.reason === "session-timeout"
          ? "The session reached its time limit or the provider is not responding. Please enable the listener again."
          : "The voice connection ended. Check your API credentials, the server, and whether another listener is already running.",
      );
  };
  ws.onerror = () => {
    if (socket === ws)
      void fail(
        "Cannot connect to the local voice server. Is scripts/run_local.sh running?",
      );
  };
}
function onFrame({ pcm, rms }) {
  if (!enabled || !context) return;
  if (context.state !== "running") {
    void fail("Audio playback was paused. Please enable the listener again.");
    return;
  }
  const now = performance.now();
  const db = Math.max(-70, 20 * Math.log10(Math.max(rms, 1e-8)));
  document.documentElement.style.setProperty(
    "--level",
    Math.min(1, (db + 70) / 70),
  );
  $("level-value").textContent = `${Math.round(db)} dB`;
  document
    .querySelector(".meter")
    .setAttribute("aria-valuenow", Math.round(db));
  if (phase === "armed") {
    preRoll.push(pcm);
    if (preRoll.length > 25) preRoll.shift();
    if (now >= cooldownUntil && trigger.update(rms, 20)) startSession();
    return;
  }
  if (!socket || finishing) return;
  // Keep PCM heartbeats flowing while preventing our own warning from re-entering STT.
  pending.push(guard.active(now) ? new ArrayBuffer(pcm.byteLength) : pcm);
  if (pending.length > 750) {
    void fail(
      "Connecting is taking too long. Please enable the listener again.",
    );
    return;
  }
  pump();
  if (db >= trigger.thresholdDb || context.currentTime < playbackUntil)
    lastActivity = now;
  if (phase === "speaking" && context.currentTime >= playbackUntil) {
    $("cancel-warning").hidden = true;
    setPhase("listening");
  }
  const seconds = Math.floor((now - startedAt) / 1000);
  $("session-time").textContent =
    `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  if (phase !== "connecting") {
    if (seconds >= maxSessionSeconds - 1)
      finishSession("Session limit reached. Finalizing the last utterance.");
    else if (now - lastActivity > 30000)
      finishSession("30 seconds of quiet. Finalizing the last utterance.");
  }
}
async function start() {
  if (!configured || enabled || preparing || stopping) return;
  preparing = true;
  const attempt = ++generation;
  $("error").hidden = true;
  $("toggle").disabled = true;
  $("toggle-label").textContent = "Allow microphone access …";
  let acquired;
  try {
    if (
      !navigator.mediaDevices?.getUserMedia ||
      !window.AudioContext ||
      !window.AudioWorkletNode
    ) {
      throw new Error("unsupported");
    }
    context = new AudioContext({ latencyHint: "interactive" });
    await context.resume();
    acquired = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: false,
      },
      video: false,
    });
    if (attempt !== generation) {
      acquired.getTracks().forEach((track) => track.stop());
      return;
    }
    microphone = acquired;
    await context.audioWorklet.addModule("/assets/capture-worklet.js");
    if (attempt !== generation) return;
    source = context.createMediaStreamSource(microphone);
    capture = new AudioWorkletNode(context, "listener-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    capture.port.onmessage = ({ data }) => onFrame(data);
    source.connect(capture);
    capture.connect(context.destination);
    microphone.getAudioTracks()[0].onended = () => {
      if (enabled)
        void fail(
          "The microphone was disconnected. Please enable the listener again.",
        );
    };
    context.onstatechange = () => {
      if (enabled && context?.state !== "running")
        void fail("Browser audio is paused. Please enable the listener again.");
    };
    enabled = true;
    $("caller-number").disabled = true;
    preparing = false;
    trigger.reset();
    cooldownUntil = 0;
    $("toggle-label").textContent = "Pause listener";
    $("toggle").disabled = false;
    setPhase("armed");
  } catch (error) {
    if (attempt !== generation) return;
    const message =
      error.name === "NotAllowedError"
        ? "Microphone access was denied. Allow microphone access for localhost in your browser settings, then try again."
        : error.name === "NotFoundError"
          ? "No microphone found. Connect a microphone and enable the listener again."
          : "The microphone could not start. Open localhost in an up-to-date Chrome, Edge, Firefox, or Safari browser and check your audio settings.";
    await fail(message);
  }
}
$("toggle").addEventListener("click", () => {
  if (enabled) void stop();
  else void start();
});
$("end-session").addEventListener("click", () =>
  finishSession("Ended by you. Finalizing the last utterance."),
);
$("sensitivity").addEventListener("input", (event) => {
  trigger.thresholdDb = Number(event.target.value);
  trigger.reset();
  $("sensitivity-value").textContent = `${trigger.thresholdDb} dB`;
  document.documentElement.style.setProperty(
    "--threshold",
    `${((trigger.thresholdDb + 70) / 70) * 100}%`,
  );
});
window.addEventListener("pagehide", () => {
  void stop();
});
try {
  const response = await fetch("/api/config");
  if (!response.ok) throw new Error("configuration-unavailable");
  const config = await response.json();
  configured = config.configured;
  maxSessionSeconds = config.max_session_seconds;
  $("caller-help").textContent = `Enter an international number (+country code), or a local number in ${config.caller_default_region}. Caller ID is not detected.`;
  $("fallback-status").textContent = config.email_status === "configured"
    ? `Fallback: ${config.fallback_email}. Automatic alerts at 50, 75 and 90; actual delivery is not yet verified.`
    : `Fallback email: ${config.email_status.replaceAll("_", " ")}. Screening works without email alerts.`;
  $("tool-resend").textContent = config.email_status.replaceAll("_", " ");
  $("toggle").disabled = !configured;
  $("toggle-label").textContent = "Enable listener";
  if (!configured) {
    $("error").textContent =
      `Finish setup: add ${config.missing.join(", ")} to .env, restart the server, and reload this page.`;
    $("error").hidden = false;
  }
} catch {
  $("error").textContent =
    "The local server is unavailable. Run scripts/run_local.sh and reload this page.";
  $("error").hidden = false;
  $("toggle-label").textContent = "Server unavailable";
}

function finishSession(reason) {
  if (!socket || finishing) return;
  if (phase === "connecting") { closeSession(true, reason); return; }
  finishing = true;
  while (pending.length && socket.readyState === WebSocket.OPEN) socket.send(pending.shift());
  addActivity("Finishing screening", reason);
  socket.send(JSON.stringify({ event: "finish" }));
  startupTimer = setTimeout(() => void fail("Final assessment timed out. Please enable the listener again."), 40000);
}
$("cancel-warning").addEventListener("click", () => {
  warningPlaybackAllowed = false;
  clearPlayback();
  guard.cancel(performance.now());
  $("cancel-warning").hidden = true;
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ event: "cancel_warning" }));
});
function resetScores(message) {
  $("overall-score").textContent = "—";
  $("risk-level").textContent = "Unavailable / waiting";
  $("risk-panel").dataset.risk = "unavailable";
  $("assessment-status").textContent = message;
  for (const key of Object.keys(criteria)) {
    $(`score-${key}`).textContent = "—";
    $(`criterion-${key}`).dataset.risk = "unavailable";
  }
}
function showScores(event) {
  $("overall-score").textContent = `${event.overall} / 100`;
  const color = riskColor(event.overall);
  $("risk-panel").dataset.risk = color;
  $("risk-level").textContent = { green: "Lower indicators", amber: "Caution", red: "High", critical: "Critical" }[color];
  $("assessment-status").textContent = `Assessment ${event.revision} · ${event.context_truncated ? "Context limit reached; opening and recent speech retained." : "Based on accumulated speech. Mistakes are possible."}`;
  for (const key of Object.keys(criteria)) {
    $(`score-${key}`).textContent = `${event.criteria[key]} / 100`;
    $(`criterion-${key}`).dataset.risk = riskColor(event.criteria[key]);
  }
}
function showCaller(caller) {
  const descriptions = {
    not_provided: "No caller number provided.", invalid: "Invalid number; lookup was not performed.",
    unavailable: "Number database unavailable. Caller risk is unknown.",
    no_match: "No match in this dataset. This does not mean the caller is safe.",
    matched: `Listed by ${caller.source} (${caller.listed_at}). Listings may be stale and caller ID can be spoofed.`,
  };
  $("caller-result").textContent = descriptions[caller.status] || "Lookup unavailable.";
  showTool({ tool: "db", status: caller.status, detail: $("caller-result").textContent });
}
