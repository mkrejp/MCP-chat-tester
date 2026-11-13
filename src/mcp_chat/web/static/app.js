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

const markers = new Map();
let map;
let answerLayer;
let activeId = "";
let searchToken = 0;
let pendingPlaces = [];
let pendingFocus = null;
let hasSearch = false;

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

function imageUrl(imageId) {
  return `/api/images?image_id=${encodeURIComponent(imageId)}`;
}

function badge(id) {
  return String(id).padStart(2, "0");
}

function openLightbox(src, alt) {
  lightboxImage.src = src;
  lightboxImage.alt = alt;
  if (!lightbox.open) lightbox.showModal();
}

function appendInline(parent, text) {
  const pattern = /(\*\*[^*]+\*\*|https?:\/\/[^\s<>"']+)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > cursor) parent.append(text.slice(cursor, start));
    const token = match[0];
    if (token.startsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      parent.append(strong);
    } else {
      const link = document.createElement("a");
      const href = token.replace(/[.,;)]+$/, "");
      link.href = href;
      link.textContent = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      parent.append(link);
      if (href.length < token.length) parent.append(token.slice(href.length));
    }
    cursor = start + token.length;
  }
  if (cursor < text.length) parent.append(text.slice(cursor));
}

function setReading(text) {
  reading.replaceChildren();
  const blocks = String(text || "").split(/\n{2,}/);
  let list = null;
  const flush = () => {
    if (list) {
      reading.append(list);
      list = null;
    }
  };
  for (const block of blocks.length ? blocks : [""]) {
    const lines = block.split("\n");
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      if (trimmed.startsWith("### ")) {
        flush();
        const heading = document.createElement("h3");
        appendInline(heading, trimmed.slice(4));
        reading.append(heading);
        continue;
      }
      if (trimmed.startsWith("- ")) {
        if (!list) list = document.createElement("ul");
        const item = document.createElement("li");
        appendInline(item, trimmed.slice(2));
        list.append(item);
        continue;
      }
      flush();
      const paragraph = document.createElement("p");
      appendInline(paragraph, trimmed);
      reading.append(paragraph);
    }
  }
  flush();
  if (!reading.childElementCount) {
    const paragraph = document.createElement("p");
    paragraph.textContent = "The archive returned no reading for this question.";
    reading.append(paragraph);
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
  conn.textContent = "Checking connection…";
  conn.className = "status";
  const response = await fetch("/api/inspection");
  if (response.status === 401) {
    window.location.assign("/auth/login");
    return;
  }
  const body = await response.json();
  conn.textContent = body.status.replaceAll("_", " ");
  conn.classList.add(body.status);
  server.textContent = body.server_url || "";
  list(tools, body.tools || [], "No tools");
  list(resources, body.resources || [], "No resources");
  list(prompts, body.prompts || [], "No prompts");
}

function setupMap(catalog) {
  if (typeof L === "undefined") {
    mapNote.textContent = "Map library did not load.";
    return;
  }
  map = L.map("map", { scrollWheelZoom: true }).setView([49.8, 15.5], 7);
  const base = L.tileLayer(catalog.base.url, {
    attribution: catalog.base.attribution,
    maxZoom: 19,
  });
  base.addTo(map);
  const overlays = {};
  answerLayer = L.layerGroup();
  overlays[catalog.answer_layer.title] = answerLayer;
  answerLayer.addTo(map);
  for (const layer of catalog.layers || []) {
    const tiles = L.tileLayer.wms(catalog.wms_url, {
      layers: layer.wms_name,
      format: "image/png",
      transparent: true,
      version: catalog.wms_version || "1.1.1",
      opacity: layer.id === "locality_links" ? 0.9 : 0.55,
    });
    overlays[layer.title] = tiles;
    if (layer.default_on) tiles.addTo(map);
  }
  L.control.layers({ [catalog.base.title]: base }, overlays, {
    collapsed: false,
  }).addTo(map);
  mapNote.textContent =
    "Numbered pins are this search. Stored localities stay in the map key until you turn them on.";
}

async function loadMap() {
  const response = await fetch("/api/map/layers");
  if (!response.ok) return;
  setupMap(await response.json());
  if (hasSearch) renderMarkers(pendingPlaces, pendingFocus);
}

function paintActive() {
  document.querySelectorAll(".card").forEach((card) => {
    card.classList.toggle("active", card.dataset.id === activeId);
  });
  document.querySelectorAll(".atlas-pin").forEach((pin) => {
    pin.classList.toggle("is-active", pin.dataset.id === activeId);
  });
}

function selectPlace(id) {
  activeId = id;
  paintActive();
  const marker = markers.get(id);
  if (marker && map) {
    const point = marker.getLatLng();
    map.setView(point, Math.max(map.getZoom(), 13));
    marker.openTooltip();
  }
  const card = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
  if (card) card.scrollIntoView({ block: "nearest" });
}

function renderFacts(places) {
  facts.replaceChildren();
  if (!places.length) {
    const empty = document.createElement("p");
    empty.className = "meta";
    empty.textContent = "This search has no notes yet.";
    facts.append(empty);
    return;
  }
  for (const place of places) {
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.id = place.id;
    const num = document.createElement("span");
    num.className = "num";
    num.textContent = badge(place.id);
    if (place.image_id) {
      const img = document.createElement("img");
      img.alt = place.title;
      img.src = imageUrl(place.image_id);
      img.addEventListener("click", (event) => {
        event.stopPropagation();
        openLightbox(img.src, place.title);
      });
      card.append(num, img);
    } else {
      const empty = document.createElement("div");
      empty.className = "placeholder";
      empty.textContent = "No picture";
      card.append(num, empty);
    }
    const copy = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = place.title || "Finding";
    const meta = document.createElement("p");
    meta.className = "meta";
    const bits = [place.kind, place.geo_source].filter(Boolean);
    if (place.lat != null && place.lon != null) {
      bits.push(`${Number(place.lat).toFixed(5)}, ${Number(place.lon).toFixed(5)}`);
    } else {
      bits.push("no GPS on this note");
    }
    meta.textContent = bits.join(" · ");
    const excerpt = document.createElement("div");
    excerpt.className = "excerpt";
    appendInline(excerpt, place.text || "");
    copy.append(title, meta, excerpt);
    if (place.url) {
      const source = document.createElement("a");
      source.className = "source-link";
      source.href = place.url;
      source.target = "_blank";
      source.rel = "noopener noreferrer";
      source.textContent = "Open source";
      source.addEventListener("click", (event) => event.stopPropagation());
      copy.append(source);
    }
    card.append(copy);
    card.addEventListener("click", () => selectPlace(place.id));
    facts.append(card);
  }
  paintActive();
}

function clearAnswerPoints() {
  if (answerLayer) {
    answerLayer.clearLayers();
    if (map && !map.hasLayer(answerLayer)) answerLayer.addTo(map);
  }
  markers.clear();
  if (map) map.closePopup();
}

function markerTip(place) {
  const tip = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = `${badge(place.id)}  ${place.title || "Finding"}`;
  const blurb = document.createElement("p");
  blurb.textContent = (place.text || "").replace(/\s+/g, " ").slice(0, 220);
  tip.append(strong, blurb);
  if (place.lat != null && place.lon != null) {
    const where = document.createElement("p");
    where.textContent = `${Number(place.lat).toFixed(5)}, ${Number(place.lon).toFixed(5)}`;
    tip.append(where);
  }
  return tip;
}

function spreadPositions(places) {
  const groups = new Map();
  for (const place of places) {
    if (place.lat == null || place.lon == null) continue;
    const key = `${Number(place.lat).toFixed(3)},${Number(place.lon).toFixed(3)}`;
    const group = groups.get(key) || [];
    group.push(place);
    groups.set(key, group);
  }
  const positioned = [];
  for (const group of groups.values()) {
    group.forEach((place, index) => {
      if (group.length === 1) {
        positioned.push({ place, lat: Number(place.lat), lon: Number(place.lon) });
        return;
      }
      const angle = (2 * Math.PI * index) / group.length;
      const radius = 0.012;
      positioned.push({
        place,
        lat: Number(place.lat) + radius * Math.cos(angle),
        lon: Number(place.lon) + radius * Math.sin(angle),
      });
    });
  }
  return positioned;
}

function renderMarkers(places, focus) {
  pendingPlaces = places;
  pendingFocus = focus || null;
  if (!map || !answerLayer) return;
  clearAnswerPoints();
  map.invalidateSize();
  const points = [];
  for (const item of spreadPositions(places)) {
    const place = item.place;
    const icon = L.divIcon({
      className: "leaflet-div-icon atlas-pin",
      html: `<span>${badge(place.id)}</span>`,
      iconSize: [32, 32],
      iconAnchor: [16, 16],
    });
    const marker = L.marker([item.lat, item.lon], {
      icon,
      zIndexOffset: 600,
      title: `${badge(place.id)} ${place.title || "Finding"}`,
    });
    marker.bindTooltip(markerTip(place), {
      direction: "top",
      offset: [0, -14],
      opacity: 1,
      className: "pin-tip",
    });
    marker.on("add", () => {
      const element = marker.getElement();
      if (!element) return;
      element.dataset.id = place.id;
      element.classList.toggle("is-active", activeId === place.id);
    });
    marker.on("click", () => selectPlace(place.id));
    marker.addTo(answerLayer);
    markers.set(place.id, marker);
    points.push([item.lat, item.lon]);
  }
  if (focus && focus.lat != null && focus.lon != null) {
    map.setView([focus.lat, focus.lon], 13);
  } else if (points.length === 1) {
    map.setView(points[0], 14);
  } else if (points.length > 1) {
    map.fitBounds(points, { padding: [36, 36], maxZoom: 14 });
  }
  mapNote.textContent = points.length
    ? `${points.length} numbered place${points.length === 1 ? "" : "s"} for this search. The number matches the note.`
    : "This search has no GPS, so the previous pins were cleared.";
}

function renderCalls(items) {
  calls.hidden = !items || !items.length;
  callsBody.replaceChildren();
  for (const call of items || []) {
    const block = document.createElement("p");
    const title = document.createElement("strong");
    title.textContent = `${call.ok ? "ok" : "error"} · ${call.tool}`;
    const args = document.createElement("pre");
    args.textContent = JSON.stringify(call.arguments, null, 2);
    block.append(title, args);
    callsBody.append(block);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = message.value.trim();
  if (!text) return;
  const token = ++searchToken;
  hasSearch = true;
  message.value = "";
  activeId = "";
  setReading("Looking through the archive…");
  facts.replaceChildren();
  clearAnswerPoints();
  pendingPlaces = [];
  pendingFocus = null;
  mapNote.textContent = "Replacing the numbered pins for this search…";
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message: text }),
  });
  if (token !== searchToken) return;
  const body = await response.json().catch(() => ({}));
  if (token !== searchToken) return;
  if (!response.ok) {
    setReading(body.detail || "The question could not be sent.");
    mapNote.textContent = "The search did not finish, so the previous pins stay cleared.";
    return;
  }
  setReading(body.reply || "");
  renderCalls(body.calls);
  renderFacts(body.places || []);
  renderMarkers(body.places || [], body.focus || null);
});

document.querySelector("#refresh").addEventListener("click", () => {
  loadInspection();
});

loadMe();
loadInspection();
loadMap();
