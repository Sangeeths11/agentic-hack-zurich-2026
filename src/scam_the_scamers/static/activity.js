const $ = (id) => document.getElementById(id);
const stages = {
  off: ["Off", "Disconnected", "Quiet"],
  armed: ["Listening", "Waiting for sound", "Quiet"],
  connecting: ["Listening", "Connecting", "Quiet"],
  listening: ["Listening", "Connected", "Quiet"],
  thinking: ["Listening", "Preparing reply", "Quiet"],
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
      ["connecting", "listening", "thinking", "speaking"].includes(next),
      next === "speaking",
    ][index];
    $(`${name}-stage`).dataset.active = String(active);
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
    addActivity("Sound detected", "Opening the voice connection.");
  else if (next === "listening" && previous === "connecting")
    addActivity("Agent connected", "The voice conversation is ready.");
  else if (next === "thinking")
    addActivity("Preparing a reply", "The agent detected the end of a turn.");
  else if (next === "speaking")
    addActivity(
      "Agent speaking",
      "Response audio is playing through your speaker.",
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
setView(window.matchMedia("(max-width: 760px)").matches ? "listener" : "split");
