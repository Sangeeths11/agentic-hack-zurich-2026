const $ = (id) => document.getElementById(id);
const preferences = ["warning_threshold", "whatsapp_threshold", "whatsapp_cooldown_seconds"];
const inputId = (name) => name.replaceAll("_", "-");

export function validPersonalSettings(value) {
  if (!value || typeof value.text !== "string" || value.text.length > 6000 ||
      !Number.isInteger(value.revision) || value.revision < 0 ||
      !["ready", "parsing", "error"].includes(value.status)) return false;
  if (!preferences.every((name) => Number.isInteger(value[name]) && value[name] >= (name === "whatsapp_cooldown_seconds" ? 0 : 1) && value[name] <= (name === "whatsapp_cooldown_seconds" ? 300 : 100))) return false;
  const profile = value.profile;
  return profile && typeof profile.listener_name === "string" &&
    typeof profile.personal_context === "string" && Array.isArray(profile.emergency_contacts) &&
    profile.emergency_contacts.length <= 10 && profile.emergency_contacts.every((contact) =>
      contact && ["name", "relationship", "phone", "whatsapp_status"].every((key) => typeof contact[key] === "string"));
}

export function createPersonalSettings({ onSaved }) {
  let sequence = 0, controller = null, timer = null, pollTimer = null;
  let saved = null, dirty = false, disposed = false, polls = 0;
  const controls = [$("personal-details"), ...preferences.map((name) => $(inputId(name)))];

  function status(message, state) {
    $("profile-save-status").textContent = message;
    $("profile-save-status").dataset.status = state;
    $("profile-retry").hidden = state !== "error";
  }
  function outputs() {
    for (const name of preferences) {
      const value = $(inputId(name)).value;
      $(`${inputId(name)}-value`).textContent = name === "whatsapp_cooldown_seconds" ? `${value}s` : `${value} / 100`;
    }
    $("personal-details-count").textContent = `${$("personal-details").value.length} / 6000`;
  }
  function fill(value) {
    $("personal-details").value = value.text;
    for (const name of preferences) $(inputId(name)).value = value[name];
    outputs();
  }
  function renderProfile(value) {
    const profile = value.profile;
    $("profile-listener-name").textContent = profile.listener_name ? `Listening for ${profile.listener_name}` : "Your name is not set yet";
    $("profile-context").textContent = profile.personal_context;
    $("profile-context").hidden = !profile.personal_context;
    $("profile-context-details").hidden = !profile.personal_context;
    $("emergency-contacts").replaceChildren();
    profile.emergency_contacts.forEach((contact, index) => {
      const card = document.createElement("article");
      card.className = "emergency-contact";
      const heading = document.createElement("h4");
      heading.textContent = `Emergency contact ${index + 1}: ${contact.phone}`;
      const name = document.createElement("p");
      name.className = "contact-identity";
      name.textContent = [contact.name, contact.relationship].filter(Boolean).join(" · ") || "Name and relationship not provided";
      const readiness = document.createElement("p");
      readiness.className = "contact-readiness";
      readiness.dataset.status = contact.whatsapp_status;
      readiness.textContent = contact.whatsapp_status === "configured"
        ? "WhatsApp configured · provider acceptance does not confirm delivery"
        : contact.whatsapp_status === "invalid_configuration"
          ? "WhatsApp configuration invalid · check this number and its matching server key"
          : "WhatsApp not configured · a matching CallMeBot key is required on the server";
      card.append(heading, name, readiness);
      $("emergency-contacts").append(card);
    });
    $("contacts-empty").hidden = profile.emergency_contacts.length > 0;
    const replacesFallback = profile.emergency_contacts.length > 0 || value.revision > 0;
    $("guardian-fallback").hidden = replacesFallback;
    $("guardian-override-status").hidden = !replacesFallback;
    $("guardian-override-status").textContent = profile.emergency_contacts.length
      ? `WhatsApp uses the ${profile.emergency_contacts.length} emergency contact${profile.emergency_contacts.length === 1 ? "" : "s"} in Personal settings under Tools & activity. Each number needs its matching server key.`
      : "No emergency contacts saved. WhatsApp alerts are disabled; fallback email is configured separately.";
  }
  function accept(value, requestId) {
    if (disposed || requestId !== sequence || !validPersonalSettings(value)) return false;
    renderProfile(value);
    if (value.status === "ready") {
      saved = value;
      polls = 0;
      dirty = false;
      fill(value);
      const applying = onSaved(value.revision);
      status(applying ? "Saved in server memory · applying to the live screening session…" : "Saved in server memory · ready for screening.", "ready");
    } else if (value.status === "error") {
      status("Settings could not be parsed. Your last accepted settings remain active. Check the text and provider connection, then retry.", "error");
    } else {
      status("Parsing personal settings… Your last accepted settings remain active.", "parsing");
      if (++polls > 30) status("Parsing is taking too long. Your last accepted settings remain active; retry when the provider is available.", "error");
      else pollTimer = setTimeout(() => void load(false, requestId), 1000);
    }
    return true;
  }
  async function request(method, body, requestId) {
    const requestController = new AbortController();
    controller = requestController;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; requestController.abort(); }, 30000);
    try {
      const response = await fetch("/api/settings", {
        method, signal: requestController.signal, cache: "no-store",
        ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
      });
      if (!response.ok) throw new Error("settings-unavailable");
      const value = await response.json();
      if (requestId !== sequence || disposed) return;
      if (method === "GET" && !dirty && validPersonalSettings(value)) fill(value);
      if (!accept(value, requestId)) throw new Error("settings-invalid");
    } catch {
      if (requestId !== sequence || disposed || requestController.signal.aborted && !timedOut) return;
      status("Personal settings could not be saved or loaded. Screening keeps its last accepted settings. Check the server and retry.", "error");
    } finally {
      clearTimeout(timeout);
      if (controller === requestController) controller = null;
    }
  }
  async function load(initial = true, requestId = sequence) {
    if (initial) status("Loading personal settings…", "parsing");
    await request("GET", null, requestId);
  }
  function save() {
    polls = 0;
    clearTimeout(timer);
    clearTimeout(pollTimer);
    controller?.abort();
    const requestId = ++sequence;
    const payload = { text: $("personal-details").value };
    for (const name of preferences) payload[name] = Number($(inputId(name)).value);
    status("Parsing personal settings… Your last accepted settings remain active.", "parsing");
    void request("POST", payload, requestId);
  }
  function edited() {
    dirty = true;
    sequence++;
    controller?.abort();
    clearTimeout(timer);
    clearTimeout(pollTimer);
    outputs();
    status("Unsaved changes · parsing starts shortly. Your last accepted settings remain active.", "pending");
    timer = setTimeout(save, 450);
  }
  for (const control of controls) control.addEventListener("input", edited);
  $("profile-retry").addEventListener("click", save);
  outputs();
  void load();
  return {
    sessionReady() {
      if (saved && onSaved(saved.revision) && !dirty) status("Saved in server memory · applying to the live screening session…", "ready");
    },
    applied(revision) {
      if (!dirty && saved?.revision === revision) status("Saved in server memory · applied to this live screening session.", "ready");
    },
    ended() {
      if (!dirty && saved) status("Saved in server memory · ready for the next screening session.", "ready");
    },
    dispose() {
      disposed = true;
      sequence++;
      controller?.abort();
      clearTimeout(timer);
      clearTimeout(pollTimer);
    },
  };
}
