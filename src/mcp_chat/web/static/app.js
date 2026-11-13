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

function openLightbox(src, alt) {
  lightboxImage.src = src;
  lightboxImage.alt = alt;
  if (!lightbox.open) lightbox.showModal();
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
  L.control.layers({ [catalog.base.title]: base }, overlays, { collapsed: false }).addTo(map);
  mapNote.textContent =
    "OpenStreetMap under the answer points. GeoServer layers are the stored localities and boundaries.";
}

async function loadMap() {
  const response = await fetch("/api/map/layers");
  if (!response.ok) return;
  setupMap(await response.json());
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
      empty.textContent = "No picture";
      card.append(empty);
    }
    const copy = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = place.title || "Fact";
    const meta = document.createElement("p");
    meta.className = "meta";
    const bits = [place.kind, place.geo_source].filter(Boolean);
    if (place.lat != null && place.lon != null) {
      bits.push(`${Number(place.lat).toFixed(5)}, ${Number(place.lon).toFixed(5)}`);
    } else {
      bits.push("no GPS on this fact");
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
    strong.textContent = place.title || "Fact";
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
  mapNote.textContent = points.length
    ? `${points.length} fact${points.length === 1 ? "" : "s"} with GPS. Toggle GeoServer layers in the map key.`
    : "This answer has no GPS. Stored locality points stay on the GeoServer layer.";
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
  message.value = "";
  reading.textContent = "Searching the knowledge server…";
  facts.replaceChildren();
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message: text }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    reading.textContent = body.detail || "The question could not be sent.";
    return;
  }
  reading.textContent = body.reply || "";
  renderCalls(body.calls);
  renderFacts(body.places || []);
  renderMarkers(body.places || []);
});

document.querySelector("#refresh").addEventListener("click", () => {
  loadInspection();
});

loadMe();
loadInspection();
loadMap();
