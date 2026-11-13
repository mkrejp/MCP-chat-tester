const reading = document.querySelector("#reading");
const facts = document.querySelector("#facts");
const calls = document.querySelector("#calls");
const callsBody = document.querySelector("#calls-body");
const form = document.querySelector("#composer");
const message = document.querySelector("#message");
const conn = document.querySelector("#conn");
const server = document.querySelector("#server");
const who = document.querySelector("#who");
const tools = document.querySelector("#tools");
const resources = document.querySelector("#resources");
const prompts = document.querySelector("#prompts");
const mapNote = document.querySelector("#map-note");
const lightbox = document.querySelector("#lightbox");
const lightboxImage = document.querySelector("#lightbox-image");
const langSelect = document.querySelector("#lang");

const markers = new Map();
let map;
let answerLayer;
let baseLayer;
let overlayLayers = {};
let layersControl = null;
let activeId = "";
let rawInspection = null;
let rawChat = null;
let rawMapCatalog = null;
let showLead = true;
let translateNotice = "";
let renderToken = 0;

function t(key, vars) {
  return window.McpI18n.t(key, vars);
}

function imageUrl(imageId) {
  return `/api/images?image_id=${encodeURIComponent(imageId)}`;
}

function openLightbox(src, alt) {
  lightboxImage.src = src;
  lightboxImage.alt = alt;
  if (!lightbox.open) lightbox.showModal();
}

async function translateTexts(texts) {
  const lang = window.McpI18n.getLanguage();
  const cleaned = texts.map((item) => (item == null ? "" : String(item)));
  const unique = [];
  const seen = new Map();
  for (const text of cleaned) {
    if (!text || seen.has(text)) continue;
    seen.set(text, unique.length);
    unique.push(text);
  }
  if (!unique.length) {
    return cleaned;
  }
  const response = await fetch("/api/translate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ target: lang, texts: unique }),
  });
  if (response.status === 401) {
    window.location.assign("/auth/login");
    throw new Error("unauthorized");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const err = new Error(body.detail || "translate failed");
    err.status = response.status;
    throw err;
  }
  const translations = body.translations || [];
  const mapped = new Map();
  unique.forEach((text, index) => {
    mapped.set(text, translations[index] ?? text);
  });
  return cleaned.map((text) => (text ? mapped.get(text) || text : text));
}

async function translateFields(items, fields) {
  const bag = [];
  const slots = [];
  for (const item of items) {
    for (const field of fields) {
      const value = item[field];
      if (typeof value === "string" && value.trim()) {
        slots.push({ item, field, index: bag.length });
        bag.push(value);
      }
    }
  }
  if (!bag.length) return items;
  try {
    const translated = await translateTexts(bag);
    translateNotice = "";
    for (const slot of slots) {
      slot.item[slot.field] = translated[slot.index];
    }
  } catch {
    translateNotice = t("reading.translate_failed");
  }
  return items;
}

function statusLabel(status) {
  const key = `status.${status}`;
  const label = t(key);
  return label === key ? status.replaceAll("_", " ") : label;
}

function list(target, items, empty) {
  target.replaceChildren();
  if (!items.length) {
    const li = document.createElement("li");
    li.textContent = empty;
    target.append(li);
    return;
  }
  for (const item of items) {
    const li = document.createElement("li");
    const name = document.createElement("strong");
    name.textContent = item.name;
    li.append(name);
    if (item.description) {
      const note = document.createElement("span");
      note.textContent = item.description;
      li.append(note);
    }
    target.append(li);
  }
}

async function loadMe() {
  const response = await fetch("/api/me");
  if (response.status === 401) {
    window.location.assign("/auth/login");
    return;
  }
  const body = await response.json();
  who.textContent = body.email || "";
}

async function loadInspection() {
  conn.textContent = t("conn.checking");
  conn.className = "status";
  const response = await fetch("/api/inspection");
  if (response.status === 401) {
    window.location.assign("/auth/login");
    return;
  }
  rawInspection = await response.json();
  await renderInspection();
}

async function renderInspection() {
  if (!rawInspection) return;
  const snapshot = structuredClone(rawInspection);
  conn.textContent = statusLabel(snapshot.status || "");
  conn.className = "status";
  if (snapshot.status) conn.classList.add(snapshot.status);
  server.textContent = snapshot.server_url || "";
  const toolItems = (snapshot.tools || []).map((item) => ({ ...item }));
  const resourceItems = (snapshot.resources || []).map((item) => ({ ...item }));
  const promptItems = (snapshot.prompts || []).map((item) => ({ ...item }));
  await translateFields(toolItems, ["description"]);
  await translateFields(resourceItems, ["description"]);
  await translateFields(promptItems, ["description"]);
  list(tools, toolItems, t("sidebar.no_tools"));
  list(resources, resourceItems, t("sidebar.no_resources"));
  list(prompts, promptItems, t("sidebar.no_prompts"));
}

function layerTitle(id, fallback) {
  const key = `map.layer.${id}`;
  const label = t(key);
  return label === key ? fallback || id : label;
}

function rebuildLayersControl(catalog) {
  if (!map || !catalog) return;
  if (layersControl) {
    map.removeControl(layersControl);
    layersControl = null;
  }
  const bases = {};
  bases[layerTitle("base", catalog.base?.title)] = baseLayer;
  const overlays = {};
  overlays[layerTitle("answer", catalog.answer_layer?.title)] = answerLayer;
  for (const layer of catalog.layers || []) {
    const tile = overlayLayers[layer.id];
    if (tile) overlays[layerTitle(layer.id, layer.title)] = tile;
  }
  layersControl = L.control.layers(bases, overlays, { collapsed: false }).addTo(map);
}

function setupMap(catalog) {
  if (typeof L === "undefined") {
    mapNote.textContent = t("map.lib_missing");
    return;
  }
  if (map) {
    rebuildLayersControl(catalog);
    if (!rawChat?.places?.length) {
      mapNote.textContent = t("map.note_ready");
    }
    return;
  }
  map = L.map("map", { scrollWheelZoom: true }).setView([49.8, 15.5], 7);
  baseLayer = L.tileLayer(catalog.base.url, {
    attribution: catalog.base.attribution,
    maxZoom: 19,
  });
  baseLayer.addTo(map);
  answerLayer = L.layerGroup();
  answerLayer.addTo(map);
  overlayLayers = {};
  for (const layer of catalog.layers || []) {
    const tiles = L.tileLayer.wms(catalog.wms_url, {
      layers: layer.wms_name,
      format: "image/png",
      transparent: true,
      version: catalog.wms_version || "1.1.1",
      opacity: layer.id === "locality_links" ? 0.9 : 0.55,
    });
    overlayLayers[layer.id] = tiles;
    if (layer.default_on) tiles.addTo(map);
  }
  rebuildLayersControl(catalog);
  mapNote.textContent = t("map.note_ready");
}

async function loadMap() {
  const response = await fetch("/api/map/layers");
  if (!response.ok) return;
  rawMapCatalog = await response.json();
  setupMap(rawMapCatalog);
}

function selectPlace(id) {
  activeId = id;
  document.querySelectorAll(".card").forEach((card) => {
    card.classList.toggle("active", card.dataset.id === id);
  });
  const marker = markers.get(id);
  if (marker && map) {
    const point = marker.getLatLng();
    map.setView(point, Math.max(map.getZoom(), 13));
    marker.openPopup();
  }
  const card = document.querySelector(`.card[data-id="${id}"]`);
  if (card) card.scrollIntoView({ block: "nearest" });
}

function renderFacts(places) {
  facts.replaceChildren();
  if (!places.length) return;
  for (const place of places) {
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.id = place.id;
    if (place.image_id) {
      const img = document.createElement("img");
      img.alt = place.title;
      img.src = imageUrl(place.image_id);
      img.addEventListener("click", (event) => {
        event.stopPropagation();
        openLightbox(img.src, place.title);
      });
      card.append(img);
    } else {
      const empty = document.createElement("div");
      empty.className = "placeholder";
      empty.textContent = t("card.no_picture");
      card.append(empty);
    }
    const copy = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = place.title || t("card.fact");
    const meta = document.createElement("p");
    meta.className = "meta";
    const bits = [place.kind, place.geo_source].filter(Boolean);
    if (place.lat != null && place.lon != null) {
      bits.push(`${Number(place.lat).toFixed(5)}, ${Number(place.lon).toFixed(5)}`);
    } else {
      bits.push(t("card.no_gps"));
    }
    meta.textContent = bits.join(" · ");
    const excerpt = document.createElement("p");
    excerpt.className = "excerpt";
    excerpt.textContent = place.text || "";
    copy.append(title, meta, excerpt);
    card.append(copy);
    card.addEventListener("click", () => selectPlace(place.id));
    facts.append(card);
  }
}

function renderMarkers(places) {
  if (!map || !answerLayer) return;
  answerLayer.clearLayers();
  markers.clear();
  const points = [];
  for (const place of places) {
    if (place.lat == null || place.lon == null) continue;
    const color = place.geo_source === "refined" ? "#0f6e6a" : "#8c3a2f";
    const marker = L.circleMarker([place.lat, place.lon], {
      radius: 8,
      color,
      weight: 2,
      fillColor: color,
      fillOpacity: 0.85,
    });
    const popup = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = place.title || t("card.fact");
    popup.append(strong);
    if (place.image_id) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = imageUrl(place.image_id);
      popup.append(img);
    }
    const blurb = document.createElement("p");
    blurb.textContent = (place.text || "").slice(0, 220);
    popup.append(blurb);
    marker.bindPopup(popup);
    marker.on("click", () => selectPlace(place.id));
    marker.addTo(answerLayer);
    markers.set(place.id, marker);
    points.push([place.lat, place.lon]);
  }
  if (points.length === 1) {
    map.setView(points[0], 14);
  } else if (points.length > 1) {
    map.fitBounds(points, { padding: [28, 28], maxZoom: 14 });
  }
  if (points.length === 1) {
    mapNote.textContent = t("map.note_gps", { count: points.length });
  } else if (points.length > 1) {
    mapNote.textContent = t("map.note_gps_plural", { count: points.length });
  } else {
    mapNote.textContent = t("map.note_no_gps");
  }
}

function renderCalls(items) {
  calls.hidden = !items || !items.length;
  callsBody.replaceChildren();
  for (const call of items || []) {
    const block = document.createElement("p");
    const title = document.createElement("strong");
    const state = call.ok ? t("calls.ok") : t("calls.error");
    title.textContent = `${state} · ${call.tool}`;
    const args = document.createElement("pre");
    args.textContent = JSON.stringify(call.arguments, null, 2);
    block.append(title, args);
    callsBody.append(block);
  }
}

async function renderChat() {
  const token = ++renderToken;
  if (!rawChat) {
    if (showLead) {
      reading.textContent = t("reading.lead");
    }
    facts.replaceChildren();
    renderCalls([]);
    return;
  }
  showLead = false;
  const payload = structuredClone(rawChat);
  reading.textContent = t("reading.translating");
  facts.replaceChildren();
  const places = (payload.places || []).map((place) => ({ ...place }));
  let reply = payload.reply || "";
  let detail = payload.detail || "";
  try {
    const texts = [];
    if (reply) texts.push(reply);
    if (detail) texts.push(detail);
    for (const place of places) {
      if (place.title) texts.push(place.title);
      if (place.text) texts.push(place.text);
    }
    const translated = await translateTexts(texts);
    translateNotice = "";
    let cursor = 0;
    if (reply) {
      reply = translated[cursor++];
    }
    if (detail) {
      detail = translated[cursor++];
    }
    for (const place of places) {
      if (place.title) place.title = translated[cursor++];
      if (place.text) place.text = translated[cursor++];
    }
  } catch {
    translateNotice = t("reading.translate_failed");
  }
  if (token !== renderToken) return;
  if (payload.error) {
    reading.textContent = detail || t("reading.send_failed");
  } else {
    reading.textContent = reply || "";
  }
  if (translateNotice) {
    reading.textContent = `${reading.textContent}\n\n${translateNotice}`.trim();
  }
  renderCalls(payload.calls);
  renderFacts(places);
  renderMarkers(places);
}

async function applyLanguage(lang) {
  await window.McpI18n.setLanguage(lang);
  if (!rawInspection) {
    conn.textContent = t("conn.checking");
  }
  if (rawMapCatalog) setupMap(rawMapCatalog);
  await renderInspection();
  await renderChat();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = message.value.trim();
  if (!text) return;
  message.value = "";
  showLead = false;
  rawChat = null;
  reading.textContent = t("reading.searching");
  facts.replaceChildren();
  renderCalls([]);
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message: text }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    rawChat = {
      error: true,
      detail: typeof body.detail === "string" ? body.detail : "",
      reply: "",
      places: [],
      calls: [],
    };
    await renderChat();
    return;
  }
  rawChat = body;
  await renderChat();
});

document.querySelector("#refresh").addEventListener("click", () => {
  loadInspection();
});

langSelect.addEventListener("change", () => {
  applyLanguage(langSelect.value);
});

async function boot() {
  await window.McpI18n.initI18n();
  langSelect.value = window.McpI18n.getLanguage();
  conn.textContent = t("conn.checking");
  reading.textContent = t("reading.lead");
  await loadMe();
  await loadInspection();
  await loadMap();
}

boot();
