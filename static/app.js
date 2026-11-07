// Food relief dispatch dashboard.
// The server owns all the logic; this page draws whatever state it's sent.

// ---------------------------------------------------------------- colours
const STATE_CLASS = {
  "Open": "s-waiting", "Prioritized": "s-waiting",
  "Resource Assigned": "s-active", "In Progress": "s-active",
  "Re-Routing": "s-warn", "Delayed": "s-warn",
  "Delivered": "s-ok", "Resolved": "s-ok",
  "Failed": "s-bad",
};
const COLOR = {
  "s-waiting": "#5e6e76", "s-active": "#1f5fad", "s-warn": "#e09b1a",
  "s-ok": "#2e8b57", "s-bad": "#c8323c",
};
const EVENT_CLASS = {
  "crisis-resolved": "ev-ok",
  "road-closed": "ev-bad", "vehicle-breakdown": "ev-bad",
  "resource-depleted": "ev-warn",
  "route-updated": "ev-active", "road-opened": "ev-active",
};
const POI_COLORS = { hub: "#1c2b33", hospital: "#c8323c", shelter: "#6b4fa3", community: "#2e8b57" };
const OTHER_POI = "#8a6d3b";   // any kind a new dataset introduces

const ROAD = { color: "#9aa8a6", weight: 4, opacity: 1, dashArray: null };
const ROAD_HWY = { color: "#7d8f8c", weight: 6, opacity: 1, dashArray: null };
const ROAD_CLOSED = { color: "#c8323c", weight: 4, opacity: 1, dashArray: "6 6" };

const PARTS = [
  ["severity", "Severity"], ["deadline", "Deadline"], ["people", "People"],
  ["location", "Location type"], ["food", "Food needed"],
];

// ---------------------------------------------------------------- state
const city = { nodes: {}, edges: {}, pois: [] };
const layers = { roads: {}, trucks: {}, sites: {}, routes: L.layerGroup() };
let state = null;
let selected = null;          // crisis id highlighted on the map
let playTimer = null;
let lastEventCount = 0;
let lastClosed = new Set();

// ---------------------------------------------------------------- helpers
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mins = (t) => Number(t).toFixed(1);

async function api(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" }, ...options,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) {
    const err = new Error((data && (data.detail || (data.errors || []).join("; "))) || "Request failed");
    err.data = data;
    throw err;
  }
  return data;
}

let toastTimer;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

// ---------------------------------------------------------------- map
const map = L.map("map", { zoomControl: true });
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
  opacity: 0.55,
}).addTo(map);
layers.routes.addTo(map);

function roadStyle(edge) {
  if (lastClosed.has(edge.id)) return ROAD_CLOSED;
  return edge.hwy ? ROAD_HWY : ROAD;
}

function drawCity(data) {
  data.nodes.forEach((n) => { city.nodes[n.id] = n; });
  data.edges.forEach((e) => { city.edges[e.id] = e; });
  city.pois = data.pois;

  data.edges.forEach((e) => {
    const a = city.nodes[e.a], b = city.nodes[e.b];
    const line = L.polyline([[a.lat, a.lng], [b.lat, b.lng]], roadStyle(e)).addTo(map);
    line.bindTooltip(
      `${esc(e.street || "Road")} (road ${e.id})<br>${mins(e.cost)} min, ${e.km.toFixed(2)} km` +
      (e.hwy ? "<br>Highway" : "") + (e.terrain && e.terrain !== "urban" ? `<br>${esc(e.terrain)}` : ""),
      { sticky: true });
    line.on("click", () => toggleRoad(e.id));
    layers.roads[e.id] = line;
  });

  data.nodes.forEach((n) => {
    L.circleMarker([n.lat, n.lng], {
      radius: 3, color: "#5e6e76", weight: 1, fillColor: "#fff", fillOpacity: 1,
    }).bindTooltip(esc(n.name)).addTo(map);
  });

  data.pois.forEach((p) => {
    const color = POI_COLORS[p.kind] || OTHER_POI;
    const isHub = p.kind === "hub";
    L.circleMarker([p.lat, p.lng], {
      radius: isHub ? 9 : 7, color: "#fff", weight: 2, fillColor: color, fillOpacity: 1,
    }).bindTooltip(`<strong>${esc(p.name)}</strong><br>${esc(p.kind)}`).addTo(map);
  });

  map.fitBounds(L.latLngBounds(data.nodes.map((n) => [n.lat, n.lng])), { padding: [24, 24] });
}

function drawOverlays() {
  // Road closures
  lastClosed = new Set(state.closed_edges);
  Object.entries(layers.roads).forEach(([id, line]) => line.setStyle(roadStyle(city.edges[id])));

  // Active routes (selected crisis drawn on top, heavier)
  layers.routes.clearLayers();
  state.crises.forEach((c) => {
    if (c.route.length < 2) return;
    const latlngs = c.route.map((id) => [city.nodes[id].lat, city.nodes[id].lng]);
    const isSel = c.request.id === selected;
    const cls = STATE_CLASS[c.state];
    L.polyline(latlngs, {
      color: COLOR[cls], weight: isSel ? 8 : 5, opacity: isSel ? 0.95 : 0.55,
      interactive: false,
    }).addTo(layers.routes);
  });

  // Crisis sites: a ring in the crisis's state colour
  const seen = new Set();
  state.crises.forEach((c) => {
    const id = c.request.id;
    seen.add(id);
    const n = city.nodes[c.request.location];
    const color = COLOR[STATE_CLASS[c.state]];
    const style = {
      radius: id === selected ? 16 : 12, color, weight: 3, fill: true,
      fillColor: color, fillOpacity: 0.15,
      dashArray: c.state === "Delayed" || c.state === "Re-Routing" ? "4 4" : null,
    };
    let m = layers.sites[id];
    if (!m) {
      m = L.circleMarker([n.lat, n.lng], style).addTo(map);
      m.on("click", () => selectCrisis(id, true));
      layers.sites[id] = m;
    } else {
      m.setStyle(style);
      m.setRadius(style.radius);
    }
    m.bindTooltip(`<strong>${esc(id)}</strong>: ${esc(c.request.crisis_type)}<br>${esc(c.state)}`);
  });
  Object.keys(layers.sites).forEach((id) => {
    if (!seen.has(id)) { map.removeLayer(layers.sites[id]); delete layers.sites[id]; }
  });

  // Trucks: several can share a spot (e.g. all parked at one depot), so
  // stack their labels vertically instead of drawing them on top of each other.
  const groups = {};
  state.trucks.forEach((t) => {
    const key = `${t.lat.toFixed(5)},${t.lng.toFixed(5)}`;
    (groups[key] = groups[key] || []).push(t.id);
  });
  state.trucks.forEach((t) => {
    const group = groups[`${t.lat.toFixed(5)},${t.lng.toFixed(5)}`];
    const offset = (group.indexOf(t.id) - (group.length - 1) / 2) * 20;
    const html = `<span class="truck-tag ${t.status}" style="transform: translate(-50%, calc(-50% + ${offset}px))">${esc(t.id)}</span>`;
    let m = layers.trucks[t.id];
    const icon = L.divIcon({ html, className: "", iconSize: null });
    if (!m) {
      m = L.marker([t.lat, t.lng], { icon, keyboard: false, zIndexOffset: 500 }).addTo(map);
      layers.trucks[t.id] = m;
    } else {
      m.setLatLng([t.lat, t.lng]);
      m.setIcon(icon);
    }
    const job = t.request_id ? `<br>On ${esc(t.request_id)}` : "";
    m.bindTooltip(`<strong>${esc(t.name)}</strong><br>${t.fridge ? "Refrigerated, " : ""}` +
                  `holds ${t.capacity}<br>${esc(t.status)}${job}`);
  });
}

async function toggleRoad(id) {
  const closed = state && state.closed_edges.includes(id);
  try {
    await api(`/api/edges/${id}/${closed ? "open" : "close"}`, { method: "POST" });
    const street = city.edges[id].street || "Road";
    toast(`${street} (road ${id}) ${closed ? "reopened" : "closed"}`);
  } catch (err) {
    toast(err.message);
  }
}

// ---------------------------------------------------------------- panels
function renderQueue() {
  const list = $("#queue");
  list.innerHTML = state.queue.map((q) => {
    const total = Math.max(q.priority, 1);
    const segs = PARTS.map(([k, label]) =>
      `<span class="p-${k}" style="width:${(q.parts[k] / total) * 100}%" title="${label}: ${q.parts[k].toFixed(1)}"></span>`
    ).join("") + (q.aging > 0
      ? `<span class="p-aging" style="width:${(q.aging / total) * 100}%" title="Waiting boost: ${q.aging.toFixed(1)}"></span>`
      : "");
    const strongest = PARTS.slice().sort((a, b) => q.parts[b[0]] - q.parts[a[0]])[0];
    return `<li>
      <div class="who">${esc(q.id)}, ${esc(q.location)}</div>
      <div class="prio" title="Priority">${q.priority.toFixed(1)}</div>
      <div class="why">
        <div class="bar" role="img" aria-label="Score breakdown">${segs}</div>
        <div class="bar-meta">Score ${q.score.toFixed(1)} + ${q.aging.toFixed(1)} for waiting. Biggest factor: ${strongest[1].toLowerCase()}.</div>
      </div>
      ${q.waiting ? `<div class="waiting">${esc(q.waiting)}</div>` : ""}
    </li>`;
  }).join("");
  $("#queue-empty").hidden = state.queue.length > 0;
  $("#count-queue").textContent = state.queue.length;
}

function renderScoreKey() {
  $("#score-key").innerHTML = PARTS.map(([k, label]) =>
    `<span><i class="p-${k}"></i>${label}</span>`).join("") +
    `<span><i class="p-aging"></i>Waiting boost</span>`;
}

function crisisCard(c) {
  const r = c.request;
  const cls = STATE_CLASS[c.state];
  const a = c.assignment;
  let detail;
  if (c.state === "Resolved") {
    detail = `Delivered by ${esc(a.truck)}`;
  } else if (c.state === "Failed") {
    detail = esc(c.history[c.history.length - 1].reason);
  } else if (a && c.eta !== null) {
    detail = `${esc(a.truck)} from ${esc(a.hub)}, arrives at ${mins(c.eta)} min (deadline ${mins(r.deadline)})`;
  } else if (c.waiting) {
    detail = `Waiting: ${esc(c.waiting)}`;
  } else {
    detail = `Deadline ${mins(r.deadline)} min`;
  }
  const open = r.id === selected;
  const timeline = open ? `<ol class="timeline">${c.history.map((h) => `
      <li class="${STATE_CLASS[h.to_state]}">
        <span class="when">${mins(h.time)}</span><span class="state">${esc(h.to_state)}</span>
        <span class="reason">${esc(h.reason)}</span>
      </li>`).join("")}</ol>` : "";
  return `<li class="crisis ${cls} ${open ? "selected" : ""}">
    <button type="button" data-crisis="${esc(r.id)}" aria-expanded="${open}">
      <span class="title">${esc(r.id)}: ${esc(r.crisis_type)} at ${esc(c.location_name)}</span>
      <span class="badge ${cls}">${esc(c.state)}</span>
      <span class="detail">${r.people_affected} people, ${r.food_needed} units${r.needs_refrigeration ? " cold" : ""}, severity ${r.severity}</span>
      <span class="detail">${detail}</span>
    </button>
    ${timeline}
  </li>`;
}

function renderCrises() {
  const order = { "s-bad": 0, "s-warn": 1, "s-active": 2, "s-waiting": 3, "s-ok": 4 };
  const sorted = state.crises.slice().sort((a, b) =>
    order[STATE_CLASS[a.state]] - order[STATE_CLASS[b.state]] ||
    a.request.deadline - b.request.deadline);
  $("#crises").innerHTML = sorted.map(crisisCard).join("");
  $("#crises-empty").hidden = state.crises.length > 0;
  const open = state.crises.filter((c) => !["Resolved", "Failed"].includes(c.state)).length;
  $("#count-crises").textContent = open;
}

function renderFleet() {
  const low = 50;
  $("#stock").innerHTML = state.stock.map((s) => `
    <tr><td>${esc(s.name)}</td>
      <td class="num ${s.normal < low ? "low" : ""}">${s.normal}</td>
      <td class="num ${s.cold < low ? "low" : ""}">${s.cold}</td></tr>`).join("");
  $("#trucks").innerHTML = state.trucks.map((t) => {
    const job = t.status === "busy" ? `on ${esc(t.request_id)}` :
                t.status === "broken" ? "broken down" : "available";
    const action = t.status === "broken"
      ? `<button class="btn small" data-repair="${esc(t.id)}">Repair</button>`
      : `<button class="btn small" data-breakdown="${esc(t.id)}">Break down</button>`;
    return `<li>
      <div><div class="name">${esc(t.name)} (${esc(t.id)})</div>
        <div class="meta">${t.fridge ? "Refrigerated, " : ""}holds ${t.capacity}, ${job}</div></div>
      ${action}
    </li>`;
  }).join("");
}

function renderFeed() {
  const events = state.events.slice().reverse();
  const fresh = Math.max(0, state.events.length - lastEventCount);
  $("#feed").innerHTML = events.map((e, i) => {
    let cls = EVENT_CLASS[e.type] || "";
    if (e.type === "state-changed") cls = { "s-ok": "ev-ok", "s-warn": "ev-warn", "s-bad": "ev-bad", "s-active": "ev-active" }[STATE_CLASS[e.data.to_state]] || "";
    return `<li class="${cls} ${i < fresh && lastEventCount ? "fresh" : ""}">
      <span class="t">${mins(e.time)} min</span><span>${esc(e.message)}</span></li>`;
  }).join("") || `<li><span class="t"></span><span class="muted">Events appear here as they happen.</span></li>`;
  lastEventCount = state.events.length;
}

function render(newState) {
  state = newState;
  $("#clock").textContent = mins(state.now);
  renderQueue();
  renderCrises();
  renderFleet();
  renderFeed();
  drawOverlays();
}

function selectCrisis(id, fromMap) {
  selected = selected === id && !fromMap ? null : id;
  if (fromMap) showTab("crises");
  render(state);
  if (selected) {
    const c = state.crises.find((x) => x.request.id === selected);
    const n = city.nodes[c.request.location];
    if (fromMap === false) map.panTo([n.lat, n.lng]);
  }
}

// ---------------------------------------------------------------- tabs
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.setAttribute("aria-selected", String(t.dataset.tab === name)));
  document.querySelectorAll(".tab-body").forEach((b) => { b.hidden = b.id !== `tab-${name}`; });
}
document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => showTab(t.dataset.tab)));

// ---------------------------------------------------------------- form
function buildForm() {
  const select = document.querySelector('[name="location"]');
  const poiNodes = new Set(city.pois.map((p) => p.node));
  const sites = city.pois.filter((p) => p.kind !== "hub");
  select.innerHTML =
    `<optgroup label="Known sites">${sites.map((p) =>
      `<option value="${p.node}" data-kind="${esc(p.kind)}">${esc(p.name)}</option>`).join("")}</optgroup>` +
    `<optgroup label="Intersections">${Object.values(city.nodes)
      .filter((n) => !poiNodes.has(n.id))
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((n) => `<option value="${n.id}">${esc(n.name)}</option>`).join("")}</optgroup>`;
  const kinds = [...new Set(city.pois.map((p) => p.kind).filter((k) => k !== "hub"))];
  $("#categories").innerHTML = kinds.map((k) => `<option value="${esc(k)}">`).join("");
  const fill = () => {
    const kind = select.selectedOptions[0] && select.selectedOptions[0].dataset.kind;
    if (kind) document.querySelector('[name="location_category"]').value = kind;
  };
  select.addEventListener("change", fill);
  fill();
}

$("#request-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const f = (name) => form.elements[name];
  form.querySelectorAll("[aria-invalid]").forEach((el) => el.removeAttribute("aria-invalid"));
  const num = (v) => (v === "" ? null : Number(v));
  const deadlineIn = num(f("deadline_in").value);
  const body = {
    location: num(f("location").value),
    crisis_type: f("crisis_type").value.trim(),
    location_category: f("location_category").value.trim(),
    people_affected: num(f("people_affected").value),
    food_needed: num(f("food_needed").value),
    needs_refrigeration: f("needs_refrigeration").checked,
    severity: num(f("severity").value),
    deadline: deadlineIn === null ? null : state.now + deadlineIn,
  };
  try {
    const res = await api("/api/requests", { method: "POST", body: JSON.stringify(body) });
    $("#form-errors").innerHTML = "";
    f("crisis_type").value = "";
    f("people_affected").value = "";
    f("food_needed").value = "";
    toast(`Request ${res.id} submitted`);
    selected = res.id;
    showTab("crises");
  } catch (err) {
    const errors = (err.data && err.data.errors) || [err.message];
    const LABELS = {
      location_category: "Location type", crisis_type: "Crisis type",
      people_affected: "People affected", food_needed: "Food needed",
      needs_refrigeration: "Needs refrigeration", submitted_at: "Submission time",
      severity: "Severity", deadline: "Deadline", location: "Location",
    };
    const readable = (e) => {
      let text = e.replace("Missing field: ", "Fill in ");
      Object.entries(LABELS).forEach(([key, label]) => { text = text.split(key).join(label); });
      return text.charAt(0).toUpperCase() + text.slice(1);
    };
    $("#form-errors").innerHTML = errors.map((e) => `<li>${esc(readable(e))}</li>`).join("");
    const fields = { deadline: "deadline_in" };
    errors.forEach((e) => {
      Object.keys(body).forEach((k) => {
        if (e.includes(k)) {
          const el = f(fields[k] || k);
          if (el) el.setAttribute("aria-invalid", "true");
        }
      });
    });
  }
});

// ---------------------------------------------------------------- controls
async function advance(minutes) {
  try {
    render(await api("/api/clock", { method: "POST", body: JSON.stringify({ minutes }) }));
  } catch (err) {
    toast(err.message);
    stopPlaying();
  }
}

function stopPlaying() {
  clearInterval(playTimer);
  playTimer = null;
  $("#play").textContent = "Play";
  $("#play").setAttribute("aria-pressed", "false");
}

$("#play").addEventListener("click", () => {
  if (playTimer) return stopPlaying();
  $("#play").textContent = "Pause";
  $("#play").setAttribute("aria-pressed", "true");
  playTimer = setInterval(() => advance(Number($("#speed").value)), 1000);
});

document.querySelectorAll("[data-skip]").forEach((b) =>
  b.addEventListener("click", () => advance(Number(b.dataset.skip))));

$("#reset").addEventListener("click", async () => {
  stopPlaying();
  selected = null;
  lastEventCount = 0;
  Object.values(layers.sites).forEach((m) => map.removeLayer(m));
  layers.sites = {};
  render(await api("/api/reset", { method: "POST" }));
  toast("Simulation reset");
});

$("#load-sample").addEventListener("click", async () => {
  try {
    const res = await api("/api/requests/sample", { method: "POST" });
    const bad = Object.keys(res.errors).length;
    toast(`Loaded ${res.accepted.length} requests${bad ? `, ${bad} rejected` : ""}`);
    showTab("crises");
  } catch (err) {
    toast(err.message);
  }
});

document.addEventListener("click", async (event) => {
  const crisis = event.target.closest("[data-crisis]");
  if (crisis) return selectCrisis(crisis.dataset.crisis, false);
  const breakId = event.target.closest("[data-breakdown]");
  const fixId = event.target.closest("[data-repair]");
  try {
    if (breakId) {
      await api(`/api/trucks/${breakId.dataset.breakdown}/breakdown`, { method: "POST" });
      toast(`${breakId.dataset.breakdown} broke down`);
    } else if (fixId) {
      await api(`/api/trucks/${fixId.dataset.repair}/repair`, { method: "POST" });
      toast(`${fixId.dataset.repair} repaired`);
    }
  } catch (err) {
    toast(err.message);
  }
});

// ---------------------------------------------------------------- live updates
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.kind === "state") render(data.state);
  };
  ws.onclose = () => setTimeout(connect, 1500);   // reconnect if the server restarts
}

async function init() {
  const data = await api("/api/map");
  $("#dataset-name").textContent = data.name;
  $("#load-sample").hidden = !data.has_sample_requests;
  drawCity(data);
  buildForm();
  renderScoreKey();
  render(await api("/api/state"));
  connect();
}

init().catch((err) => toast(`Could not load: ${err.message}`));
