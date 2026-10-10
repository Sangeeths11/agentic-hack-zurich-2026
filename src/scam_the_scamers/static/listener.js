import { LevelTrigger, decodePcm } from "./audio.js";

const $ = (id) => document.getElementById(id);
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
    "BEREIT ZUM ZUHÖREN",
    "Einmal einschalten. Dann einfach sprechen.",
    "Aktiviere das Mikrofon, um den Listener bereitzumachen.",
  ],
  armed: [
    "LISTENER AKTIV",
    "Ich höre hin.",
    "Sobald jemand spricht, beginnt das Gespräch automatisch.",
  ],
  connecting: [
    "STIMME ERKANNT",
    "Ich bin gleich da.",
    "Der Assistent verbindet sich. Der Satzanfang wird kurz gepuffert.",
  ],
  listening: [
    "GESPRÄCH AKTIV",
    "Ich höre zu.",
    "Sprich ganz normal. Die Antwort kommt über den Laptop.",
  ],
  thinking: [
    "GESPRÄCH AKTIV",
    "Einen Moment …",
    "Der Assistent bereitet seine Antwort vor.",
  ],
  speaking: [
    "GESPRÄCH AKTIV",
    "Dein Assistent spricht.",
    "Du kannst jederzeit dazwischenreden.",
  ],
  error: [
    "LISTENER PAUSIERT",
    "Kurz nachsehen.",
    "Behebe den Hinweis und aktiviere den Listener erneut.",
  ],
};
function setPhase(next) {
  if (phase === next && $("orb").dataset.state === next) return;
  phase = next;
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
function closeSession(rearm = true) {
  clearTimeout(startupTimer);
  const old = socket;
  socket = null;
  if (old) {
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
  if (rearm && enabled) setPhase("armed");
}
async function stop() {
  if (stopping) return;
  stopping = true;
  $("toggle").disabled = true;
  enabled = false;
  preparing = false;
  generation++;
  closeSession(false);
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
  $("toggle-label").textContent = "Listener aktivieren";
  $("toggle").disabled = !configured;
  stopping = false;
  setPhase("off");
}
async function fail(message) {
  await stop();
  $("error").textContent = message;
  $("error").hidden = false;
  setPhase("error");
}
function pump() {
  if (!socket || socket.readyState !== WebSocket.OPEN || phase === "connecting")
    return;
  if (socket.bufferedAmount > 96000) {
    void fail("Die Audioverbindung ist zu langsam. Bitte erneut aktivieren.");
    return;
  }
  // Catch up the bounded startup buffer gradually, preserving order without flooding the server.
  for (let i = 0; i < 4 && pending.length; i++) socket.send(pending.shift());
}
function play(data) {
  if (!context || context.state !== "running") return;
  if (!data.byteLength || data.byteLength % 2) {
    void fail("Ungültiges Antwort-Audio. Bitte erneut aktivieren.");
    return;
  }
  if (playbackUntil - context.currentTime > 12) {
    void fail(
      "Die Sprachausgabe ist zu weit im Rückstand. Bitte erneut aktivieren.",
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
        "Der Assistent konnte nicht rechtzeitig starten. Prüfe Server und API-Zugang.",
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
      void fail("Ungültige Serverantwort.");
      return;
    }
    if (event.event === "ready") {
      if (event.input_rate !== 24000 || event.output_rate !== 48000) {
        void fail("Das Audioformat passt nicht zum Listener.");
        return;
      }
      clearTimeout(startupTimer);
      setPhase("listening");
      pump();
    } else if (event.event === "clear") {
      clearPlayback();
      setPhase("listening");
    } else if (event.event === "thinking") {
      setPhase("thinking");
      lastActivity = performance.now();
    }
  };
  ws.onclose = (event) => {
    if (socket !== ws) return;
    if (event.code === 1000) closeSession();
    else
      void fail(
        event.reason === "session-timeout"
          ? "Die Sitzung hat ihr Zeitlimit erreicht oder der Anbieter antwortet nicht. Bitte erneut aktivieren."
          : "Die Sprachverbindung wurde beendet. Prüfe API-Zugang, Server und ob bereits ein anderer Listener läuft.",
      );
  };
  ws.onerror = () => {
    if (socket === ws)
      void fail(
        "Keine Verbindung zum lokalen Sprachserver. Läuft scripts/run_local.sh?",
      );
  };
}
function onFrame({ pcm, rms }) {
  if (!enabled || !context) return;
  if (context.state !== "running") {
    void fail(
      "Die Audioausgabe wurde pausiert. Bitte den Listener erneut aktivieren.",
    );
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
  if (!socket) return;
  pending.push(pcm);
  if (pending.length > 750) {
    void fail(
      "Der Verbindungsaufbau dauert zu lange. Bitte erneut aktivieren.",
    );
    return;
  }
  pump();
  if (db >= trigger.thresholdDb || context.currentTime < playbackUntil)
    lastActivity = now;
  if (phase === "speaking" && context.currentTime >= playbackUntil)
    setPhase("listening");
  const seconds = Math.floor((now - startedAt) / 1000);
  $("session-time").textContent =
    `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  if (
    phase !== "connecting" &&
    (now - lastActivity > 30000 || seconds >= maxSessionSeconds - 1)
  )
    closeSession();
}
async function start() {
  if (!configured || enabled || preparing || stopping) return;
  preparing = true;
  const attempt = ++generation;
  $("error").hidden = true;
  $("toggle").disabled = true;
  $("toggle-label").textContent = "Mikrofon freigeben …";
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
        void fail("Das Mikrofon wurde getrennt. Bitte erneut aktivieren.");
    };
    context.onstatechange = () => {
      if (enabled && context?.state !== "running")
        void fail("Browser-Audio pausiert. Bitte erneut aktivieren.");
    };
    enabled = true;
    preparing = false;
    trigger.reset();
    cooldownUntil = 0;
    $("toggle-label").textContent = "Listener pausieren";
    $("toggle").disabled = false;
    setPhase("armed");
  } catch (error) {
    if (attempt !== generation) return;
    const message =
      error.name === "NotAllowedError"
        ? "Mikrofonzugriff nicht erlaubt. Erlaube das Mikrofon in den Browser-Einstellungen für localhost und versuche es erneut."
        : error.name === "NotFoundError"
          ? "Kein Mikrofon gefunden. Verbinde ein Mikrofon und aktiviere den Listener erneut."
          : "Das Mikrofon konnte nicht starten. Öffne localhost in einem aktuellen Chrome, Edge, Firefox oder Safari und prüfe die Audio-Einstellungen.";
    await fail(message);
  }
}
$("toggle").addEventListener("click", () => {
  if (enabled) void stop();
  else void start();
});
$("end-session").addEventListener("click", () => closeSession());
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
  $("toggle").disabled = !configured;
  $("toggle-label").textContent = "Listener aktivieren";
  if (!configured) {
    $("error").textContent =
      `Noch kurz einrichten: ${config.missing.join(", ")} in .env ergänzen, den Server neu starten und diese Seite neu laden.`;
    $("error").hidden = false;
  }
} catch {
  $("error").textContent =
    "Der lokale Server ist nicht erreichbar. Starte scripts/run_local.sh und lade die Seite neu.";
  $("error").hidden = false;
  $("toggle-label").textContent = "Server nicht erreichbar";
}
