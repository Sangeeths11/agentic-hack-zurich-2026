const $ = (id) => document.getElementById(id);
const stages = {
  off: ["Off", "Disconnected", "Quiet"],
  armed: ["Listening", "Waiting for sound", "Quiet"],
  connecting: ["Listening", "Connecting", "Quiet"],
  listening: ["Listening", "Connected", "Quiet"],
  thinking: ["Listening", "Connected", "Quiet"],
  speaking: ["Listening", "Connected", "Playing audio"],
  error: ["Off", "Disconnected", "Quiet"],
};
const timeFormat = new Intl.DateTimeFormat("en", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function addActivity(title, detail, kind = "info") {
  const row = document.createElement("li");
  row.dataset.kind = kind;
  const marker = document.createElement("span");
  marker.className = "activity-marker";
  marker.setAttribute("aria-hidden", "true");
  const copy = document.createElement("div");
  const heading = document.createElement("strong");
  heading.textContent = title;
  const description = document.createElement("p");
  description.textContent = detail;
  copy.append(heading, description);
  const time = document.createElement("time");
  const now = new Date();
  time.dateTime = now.toISOString();
  time.textContent = timeFormat.format(now);
  row.append(marker, copy, time);
  $("activity-log").prepend(row);
  while ($("activity-log").children.length > 60)
    $("activity-log").lastElementChild.remove();
  $("activity-empty").hidden = true;
  $("clear-activity").disabled = false;
  $("activity-announcement").textContent = `${title}. ${detail}`;
}

export function showActivityPhase(next, previous) {
  const labels = stages[next];
  ["microphone", "agent", "speaker"].forEach((name, index) => {
    $(`${name}-status`).textContent = labels[index];
    const active = [
      !["off", "error"].includes(next),
      ["listening", "thinking", "speaking"].includes(next),
      next === "speaking",
    ][index];
    $(`${name}-stage`).dataset.active = String(active);
    setEdge(name === "agent" ? "backend" : name, active);
  });
  const live = !["off", "error"].includes(next);
  $("activity-status").textContent = live
    ? "Live"
    : next === "error"
      ? "Needs attention"
      : "Standby";
  $("activity-status").dataset.live = String(live);
  if (next === "armed" && ["off", "error"].includes(previous))
    addActivity("Listener enabled", "Microphone active. Waiting for sound.");
  else if (next === "connecting")
    addActivity("Sound detected", "Opening the screening connection.");
  else if (next === "listening" && previous === "connecting")
    addActivity("Screening connected", "Gradium STT is ready. Scores will follow speech.");
  else if (next === "thinking")
    addActivity("Assessing call", "Gemini is assessing the call context.");
  else if (next === "speaking")
    addActivity(
      "Warning playing",
      "A caution is playing. Microphone forwarding is briefly suppressed.",
    );
  else if (next === "off" && previous !== "off")
    addActivity("Listener paused", "Microphone and audio playback stopped.");
}

$("clear-activity").addEventListener("click", () => {
  $("activity-log").replaceChildren();
  $("activity-empty").textContent =
    "Activity cleared. New events will appear here.";
  $("activity-empty").hidden = false;
  $("clear-activity").disabled = true;
  $("activity-announcement").textContent = "Activity cleared.";
});

const viewButtons = [...document.querySelectorAll(".view-switch button")];
function setView(view) {
  $("workspace").dataset.view = view;
  $("listener-panel").hidden = view === "tools";
  $("tools-panel").hidden = view === "listener";
  viewButtons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  });
}
viewButtons.forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});
setView("listener");

export function showTool(event) {
  const tool = event.tool === "db" ? "lookup" : event.tool;
  const labels = { backend: "Backend", stt: "Gradium STT", gemini: "Gemini", lookup: "Number database", scoring: "Local scoring", tts: "Gradium TTS", resend: "Resend email", callmebot: "CallMeBot WhatsApp" };
  if (!labels[tool] || typeof event.status !== "string") return;
  const status = $(tool === "backend" ? "agent-status" : `tool-${tool === "lookup" ? "db" : tool}`);
  const node = $(tool === "backend" ? "agent-stage" : `node-${tool}`);
  status.textContent = event.status.replaceAll("_", " ");
  status.dataset.status = event.status;
  node.dataset.status = event.status;
  const active = ["connecting", "connected", "streaming", "assessing", "checking", "calculating", "synthesizing", "sending"].includes(event.status);
  node.dataset.active = String(active);
  setEdge(tool, active);
  addActivity(`${labels[tool]} · ${status.textContent}`, typeof event.detail === "string" ? event.detail : "", event.status === "error" ? "error" : "info");
}

function setEdge(tool, active) {
  document.querySelectorAll(`.flow-lines [data-to="${tool}"]`).forEach((edge) => {
    edge.dataset.active = String(active);
  });
}

export function resetTools(disconnected = false) {
  for (const tool of ["lookup", "stt", "gemini", "scoring", "tts", "resend", "callmebot"]) {
    const node = $(`node-${tool}`);
    node.dataset.active = "false";
    delete node.dataset.status;
    setEdge(tool, false);
    const status = $(`tool-${tool === "lookup" ? "db" : tool}`);
    if (["resend", "callmebot"].includes(tool)) {
      if (disconnected && status.dataset.status === "sending") {
        status.textContent = "Stopped · delivery unknown";
        status.dataset.status = "unknown";
        node.dataset.status = "unknown";
      }
      continue; // Keep configuration and completed outcomes visible.
    }
    delete status.dataset.status;
    if (tool === "lookup") {
      if (!disconnected) status.textContent = "Not checked";
    }
    else status.textContent = disconnected ? "Disconnected" : "Waiting";
  }
}
