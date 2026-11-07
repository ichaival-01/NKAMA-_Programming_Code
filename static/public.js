// Public request page: send a request, then follow ONLY your own requests.
// The tracking codes are kept in this browser; the server only answers for
// codes you hold, so nobody can see anyone else's request.

const STORE_KEY = "food-request-codes";
const STATE_CLASS = {
  "Open": "st-active", "Prioritized": "st-active", "Resource Assigned": "st-active",
  "In Progress": "st-active", "Scheduled": "st-active",
  "Re-Routing": "st-warn", "Delayed": "st-warn",
  "Delivered": "st-ok", "Resolved": "st-ok",
  "Failed": "st-bad",
};
const COLOR = { "st-active": "#1f5fad", "st-warn": "#e09b1a", "st-ok": "#2e8b57", "st-bad": "#c8323c" };

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mins = (t) => Number(t).toFixed(0);

const city = { nodes: {} };
let selected = null;
let latest = [];

// ---------------------------------------------------------------- saved codes
function loadCodes() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || []; } catch (e) { return memoryCodes; }
}
let memoryCodes = [];
function saveCodes(list) {
  memoryCodes = list;
  try { localStorage.setItem(STORE_KEY, JSON.stringify(list)); } catch (e) { /* private mode */ }
}
function addCode(code) {
  const list = loadCodes().filter((c) => c !== code);
  list.unshift(code);
  saveCodes(list.slice(0, 20));
}
function removeCode(code) {
  saveCodes(loadCodes().filter((c) => c !== code));
  if (selected === code) selected = null;
  refresh();
}

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* empty */ }
  if (!res.ok) {
    const err = new Error((data && (data.detail || (data.errors || []).join("; "))) || "Something went wrong");
    err.data = data;
    throw err;
  }
  return data;
}

// ---------------------------------------------------------------- map (your delivery only)
const map = L.map("map", { zoomControl: false, attributionControl: true });
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: "&copy; OpenStreetMap contributors", opacity: 0.6,
}).addTo(map);
const overlay = L.layerGroup().addTo(map);
let lastFitted = null;

function drawMap(r) {
  overlay.clearLayers();
  if (!r) { $("#map-card").hidden = true; return; }
  $("#map-card").hidden = false;
  map.invalidateSize();
  const site = city.nodes[r.location];
  const color = COLOR[STATE_CLASS[r.state]];
  const points = [[site.lat, site.lng]];
  if (r.route.length > 1) {
    const line = r.route.map((id) => [city.nodes[id].lat, city.nodes[id].lng]);
    L.polyline(line, { color, weight: 6, opacity: 0.85 }).addTo(overlay);
    points.push(...line);
  }
  L.circleMarker([site.lat, site.lng], {
    radius: 11, color: "#1c2b33", weight: 4, fillColor: "#fff", fillOpacity: 1,
  }).bindTooltip("Your location").addTo(overlay);
  if (r.truck) {
    L.marker([r.truck.lat, r.truck.lng], {
      icon: L.divIcon({ className: "", iconSize: null,
        html: `<span class="truck-tag" style="background:${color}">${esc(r.truck.name)}</span>` }),
      keyboard: false,
    }).addTo(overlay);
    points.push([r.truck.lat, r.truck.lng]);
  }
  // Re-frame only when switching requests, so the map doesn't jump every update.
  if (lastFitted !== r.code) {
    map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 16 });
    lastFitted = r.code;
  }
}

// ---------------------------------------------------------------- your requests
function card(r) {
  const cls = STATE_CLASS[r.state] || "st-active";
  const steps = r.steps.map((label, i) => {
    const state = i < r.step ? "done" : i === r.step ? "current" : "";
    return `<li class="${state}" ${i === r.step ? 'aria-current="step"' : ""}>${esc(label)}</li>`;
  }).join("");
  const away = r.minutes_away;
  const late = r.eta !== null && r.eta > r.deadline;
  const eta = away !== null && !r.finished
    ? `<p class="req-eta">${away < 1 ? "Arriving now" : `Arriving in ${Math.ceil(away)} min`}
         <small>${late ? "later than you needed" : "within the time you needed"}</small></p>`
    : "";
  const history = r.timeline.length ? `
    <details class="history">
      <summary>Full history</summary>
      <ol>${r.timeline.slice().reverse().map((h) =>
        `<li><span class="t">${mins(h.time)} min</span><strong>${esc(h.title)}.</strong> ${esc(h.detail)}</li>`).join("")}</ol>
    </details>` : "";
  return `<article class="req ${cls} ${r.code === selected ? "selected" : ""}" data-code="${esc(r.code)}">
    <div class="req-head">
      <h3 class="req-title">${esc(r.title)}</h3>
      <span class="req-code">${esc(r.code)}</span>
    </div>
    <p class="req-where">${esc(r.crisis_type)} at ${esc(r.location_name)}: ${r.food_needed} units for ${r.people_affected} people</p>
    <ol class="steps" aria-label="Progress">${steps}</ol>
    <p class="req-message">${esc(r.message)}</p>
    ${eta}
    ${history}
    <div class="req-actions">
      <button class="linkish" data-show="${esc(r.code)}">Show on map</button>
      ${r.finished ? `<button class="linkish" data-forget="${esc(r.code)}">Remove from this list</button>` : ""}
    </div>
  </article>`;
}

function render() {
  $("#requests").innerHTML = latest.map(card).join("");
  $("#no-requests").hidden = latest.length > 0;
  if (!selected && latest.length) selected = latest[0].code;
  drawMap(latest.find((r) => r.code === selected));
}

async function refresh() {
  const tracking = loadCodes();
  if (!tracking.length) { latest = []; render(); return; }
  try {
    const res = await post("/api/public/status", { tracking_codes: tracking });
    latest = res.requests;
    // Forget codes the server no longer knows (e.g. after a reset)
    const known = new Set(latest.map((r) => r.code));
    saveCodes(tracking.filter((c) => known.has(c)));
    render();
  } catch (err) { /* keep showing the last update; try again shortly */ }
}

document.addEventListener("click", (e) => {
  const show = e.target.closest("[data-show]");
  const forget = e.target.closest("[data-forget]");
  if (show) { selected = show.dataset.show; lastFitted = null; render(); $("#map-card").scrollIntoView({ behavior: "smooth", block: "nearest" }); }
  if (forget) removeCode(forget.dataset.forget);
});

$("#track-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const code = $("#track-code").value.trim().toUpperCase();
  $("#track-error").textContent = "";
  if (!code) return;
  const res = await post("/api/public/status", { tracking_codes: [code] }).catch(() => ({ requests: [] }));
  if (!res.requests.length) {
    $("#track-error").textContent = "No request matches that code. Check it and try again.";
    return;
  }
  addCode(res.requests[0].code);
  selected = res.requests[0].code;
  lastFitted = null;
  $("#track-code").value = "";
  refresh();
});

// ---------------------------------------------------------------- the request form
function buildForm(data) {
  const select = document.querySelector('[name="location"]');
  const poiNodes = new Set(data.pois.map((p) => p.node));
  const sites = data.pois.filter((p) => p.kind !== "hub");
  select.innerHTML =
    `<option value="" disabled selected>Choose a place or intersection</option>` +
    `<optgroup label="Known places">${sites.map((p) =>
      `<option value="${p.node}" data-kind="${esc(p.kind)}">${esc(p.name)}</option>`).join("")}</optgroup>` +
    `<optgroup label="Street intersections">${data.nodes
      .filter((n) => !poiNodes.has(n.id))
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((n) => `<option value="${n.id}">${esc(n.name)}</option>`).join("")}</optgroup>`;
  const kinds = [...new Set(["shelter", "community", "hospital", "school",
    ...data.pois.map((p) => p.kind).filter((k) => k !== "hub")])];
  $("#categories").innerHTML = kinds.map((k) => `<option value="${esc(k)}">`).join("");
  select.addEventListener("change", () => {
    const kind = select.selectedOptions[0] && select.selectedOptions[0].dataset.kind;
    if (kind) document.querySelector('[name="location_category"]').value = kind;
  });
}

const LABELS = {
  location_category: "Kind of place", crisis_type: "What happened",
  people_affected: "Number of people", food_needed: "Amount of food",
  needs_refrigeration: "Cold food", severity: "How serious", deadline: "Needed within",
  location: "Where",
};

$("#request-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const f = (n) => form.elements[n];
  form.querySelectorAll("[aria-invalid]").forEach((el) => el.removeAttribute("aria-invalid"));
  const num = (v) => (v === "" ? null : Number(v));
  const within = num(f("deadline_in").value);
  // "Needed within N minutes": the server turns this into a clock time.
  const body = {
    location: num(f("location").value),
    crisis_type: f("crisis_type").value.trim(),
    location_category: f("location_category").value.trim(),
    people_affected: num(f("people_affected").value),
    food_needed: num(f("food_needed").value),
    needs_refrigeration: f("needs_refrigeration").checked,
    severity: num(form.querySelector('[name="severity"]:checked').value),
    deadline_in: within,
  };
  try {
    const res = await post("/api/public/requests", body);
    $("#form-errors").innerHTML = "";
    form.reset();
    f("deadline_in").value = 60;
    addCode(res.code);
    selected = res.code;
    lastFitted = null;
    $("#sent-code").textContent = res.code;
    $("#sent").hidden = false;
    $("#sent-ok").focus();
    refresh();
  } catch (err) {
    const errors = (err.data && err.data.errors) || [err.message];
    const readable = (msg) => {
      let text = msg.replace("Missing field: ", "Please fill in: ");
      Object.entries(LABELS).forEach(([k, label]) => { text = text.split(k).join(label.toLowerCase()); });
      return text.charAt(0).toUpperCase() + text.slice(1);
    };
    $("#form-errors").innerHTML = errors.map((m) => `<li>${esc(readable(m))}</li>`).join("");
    const fields = { deadline: "deadline_in" };
    errors.forEach((m) => Object.keys(LABELS).forEach((k) => {
      if (m.includes(k)) { const el = f(fields[k] || k); if (el && el.setAttribute) el.setAttribute("aria-invalid", "true"); }
    }));
  }
});

$("#sent-ok").addEventListener("click", () => {
  $("#sent").hidden = true;
  $("#mine-title").scrollIntoView({ behavior: "smooth" });
});

// ---------------------------------------------------------------- start
async function init() {
  const data = await (await fetch("/api/map")).json();
  data.nodes.forEach((n) => { city.nodes[n.id] = n; });
  $("#city").textContent = data.name;
  buildForm(data);
  await refresh();
  setInterval(refresh, 3000);     // progress updates every few seconds
}
init();
