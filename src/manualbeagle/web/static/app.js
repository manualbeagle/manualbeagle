// Manual Beagle's web UI: one page, plain JavaScript, talking to the JSON API under /api.
// Everything from the server or a web site is put on the page with textContent, never as HTML.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);

// el("a", {href: "#x", class: "y"}, "text", child): an element with attributes and children.
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) {
    if (k === null || k === undefined || k === false) continue;
    e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return e;
}

// A status as a glyph and a word; the class only adds colour.
function st(kind, text) {
  return el("span", { class: `st ${kind}` }, text);
}

// A link to somewhere outside Manual Beagle: only http(s), opened in a new tab without a referrer.
function outLink(url, text) {
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return el("span", { class: "url" }, url);
  } catch {
    return el("span", { class: "url" }, url);
  }
  return el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text || url);
}

function host(url) {
  try { return new URL(url).hostname; } catch { return ""; }
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  let data = null;
  try { data = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}

function errorBox(e) {
  return el("p", { class: "error", role: "alert" }, "✗ ", e.message || String(e));
}

function busy(text) {
  return el("p", { class: "muted" }, "… ", text);
}

const S = { kinds: {}, status: null, docs: null, brands: [], photos: null, lastFind: null };

function kindLabel(k) {
  return (k && S.kinds[k]) || "Document";
}

// --- Routing: #find, #library, #ask, #batch, #homebox, #maintenance, #settings, #read/<sha>/<page>?q=words

const VIEWS = ["find", "library", "ask", "batch", "homebox", "maintenance", "settings", "read"];

function route() {
  const hash = location.hash.slice(1) || "find";
  const [path, query] = hash.split("?");
  const parts = path.split("/");
  const view = VIEWS.includes(parts[0]) ? parts[0] : "find";
  for (const v of VIEWS) $(`#view-${v}`).hidden = v !== view;
  for (const a of document.querySelectorAll("nav a")) {
    const on = a.dataset.view === view || (view === "read" && a.dataset.view === "library");
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  if (view === "library") showLibrary();
  if (view === "ask") showAsk();
  if (view === "homebox") showHomebox();
  if (view === "maintenance") showMaintenance();
  if (view === "settings") showSettings();
  if (view === "read") {
    const q = new URLSearchParams(query || "").get("q") || "";
    showReader(parts[1], parseInt(parts[2] || "1", 10) || 1, q);
  }
  if (view === "find") $("#find-form [name=brand]").focus({ preventScroll: true });
}

// --- Status line

async function loadStatus() {
  try {
    const s = await api("/api/status");
    S.status = s;
    for (const k of s.kinds) S.kinds[k.value] = k.label;
    const sel = $("#find-form [name=kind]");
    if (sel.options.length === 1) {
      for (const k of s.kinds) sel.append(el("option", { value: k.value }, k.label));
    }
    $("#status").replaceChildren(
      `${s.documents} documents · ${s.pages.toLocaleString()} pages · `,
      s.ocr ? st("ok", "✓ OCR") : st("none", "– no OCR"),
      " · ",
      s.search ? st("ok", "✓ web search") : st("none", "– no web search"),
      ` · v${s.version}`,
    );
  } catch (e) {
    $("#status").replaceChildren(st("bad", "✗ can't reach the server"));
  }
}

// --- Find

$("#find-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const body = {
    brand: f.brand.value, model: f.model.value, year: f.year.value || null,
    kind: f.kind.value || null, search: f.search.value,
  };
  const out = $("#find-out");
  const btn = f.querySelector("button");
  btn.disabled = true;
  out.replaceChildren(busy(`Looking up ${body.brand} ${body.model}…`));
  try {
    const res = await api("/api/find", body);
    // Kept for Add to Homebox, which reads the kind of product ("Dishwasher") from the titles found.
    S.lastFind = { brand: body.brand, model: body.model, titles: res.candidates.slice(0, 12).map((c) => c.title || "") };
    out.replaceChildren(renderFind(res, body));
  } catch (e) {
    out.replaceChildren(errorBox(e));
  } finally {
    btn.disabled = false;
  }
});

function renderFind(res, asked) {
  const q = res.query;
  const brand = q.canonical_brand || q.brand;
  const sources = el("ul", { class: "sources" });
  for (const r of res.adapters) {
    const state = r.ok ? st("ok", "✓ ok") : r.blocked ? st("bad", "✗ blocked") : st("bad", "✗ error");
    sources.append(el("li", {}, `adapter ${r.adapter}: `, state, `, ${r.count} found`, r.error ? ` — ${r.error}` : ""));
  }
  if (!res.adapters.length) sources.append(el("li", {}, st("none", "– no adapter for this brand")));
  if (res.searched) {
    sources.append(el("li", {}, "web search: ",
      res.search_error ? st("bad", `✗ ${res.search_error}`) : st("ok", "✓ ok")));
  } else {
    sources.append(el("li", {}, "web search: ", st("none", "– not run")));
  }
  const head = el("div", { class: "panel" },
    el("strong", {}, `${brand} ${q.model}`, q.year ? ` (${q.year})` : ""),
    el("span", { class: "muted" }, `   variants: ${q.variants.join(", ")}`),
    sources);
  if (!res.candidates.length) {
    return el("div", {}, head, el("p", {}, st("bad", "✗ nothing found"),
      " Try the model without its suffix, or another spelling of the brand."));
  }
  const list = el("ol", { class: "cands panel" });
  res.candidates.slice(0, 12).forEach((c, i) => {
    list.append(renderCandidate(c, i, res.in_library[c.url], { brand: asked.brand, model: asked.model }));
  });
  return el("div", {}, head, list);
}

function renderCandidate(c, i, inLib, who) {
  const result = el("div", { class: "result", "aria-live": "polite" });
  const actions = el("div", { class: "actions" });
  if (inLib) {
    actions.append(el("a", { href: `#read/${inLib}/1` }, "Read →"));
  } else if (!c.link_only) {
    const b = el("button", { type: "button", class: "small" }, "Fetch");
    b.addEventListener("click", () => fetchCandidate(c, who, b, result));
    actions.append(b);
  }
  const badges = [];
  if (c.link_only) badges.push(st("warn", "▲ link only: open it yourself"));
  if (inLib) badges.push(st("ok", "✓ in library"));
  return el("li", { class: "cand" },
    el("span", { class: "rank", title: i === 0 ? "best" : null }, i === 0 ? "★1" : String(i + 1)),
    el("span", { class: "score", title: "score" }, Math.round(c.score)),
    el("div", {},
      el("div", { class: "title" }, outLink(c.page_url && c.link_only ? c.page_url : c.url, c.title || c.url)),
      el("div", { class: "meta" },
        el("span", {}, kindLabel(c.kind)), el("span", {}, host(c.url)), el("span", {}, c.source), ...badges),
      el("details", {}, el("summary", {}, "why this score"), (c.reasons || []).join("; ") || "–")),
    actions,
    result);
}

async function fetchCandidate(c, who, btn, out) {
  btn.disabled = true;
  out.replaceChildren(busy("Downloading and reading every page…"));
  try {
    const d = await api("/api/fetch", { candidate: c, brand: who.brand, model: who.model });
    const named = d.check_score > 0;
    out.replaceChildren(
      named ? st("ok", "✓ saved") : st("warn", "▲ saved, but its text doesn't name the model"),
      ` ${d.pages} pages${d.ocr ? ", OCR" : ""} · `,
      el("a", { href: `#read/${d.sha256}/1` }, "Read →"),
      el("div", { class: "muted" }, "check: ", d.check_reasons.join("; ") || "–"));
    btn.remove();
    S.docs = null;
    loadStatus();
  } catch (e) {
    out.replaceChildren(errorBox(e));
    btn.disabled = false;
  }
}

// --- Scan: photograph a rating plate, read it on the server, confirm the brand and model, then Find.

const SCAN_EDGE = 2400; // pixels on the long side: enough for small print, about 1 MB as JPEG

// The photo as an upright JPEG, at most SCAN_EDGE on its long side. Drawing it on a canvas applies the camera's
// rotation and leaves its metadata (location, device) behind; the server strips metadata again anyway.
async function shrink(file) {
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    throw new Error("this browser can't open that photo; take it again as a JPEG");
  }
  const scale = Math.min(1, SCAN_EDGE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return new Promise((resolve, reject) => canvas.toBlob(
    (b) => (b ? resolve(b) : reject(new Error("couldn't encode the photo"))), "image/jpeg", 0.9));
}

async function sendPhoto(blob) {
  const r = await fetch("/api/nameplate", { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob });
  let data = null;
  try { data = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}

// Both photo inputs, the camera and the photo library, read a plate the same way.
async function scanPhoto(ev) {
  const file = ev.target.files[0];
  ev.target.value = ""; // the same photo can be chosen again
  if (!file) return;
  const out = $("#scan-out");
  out.replaceChildren(busy("Reading the rating plate…"));
  try {
    const res = await sendPhoto(await shrink(file));
    out.replaceChildren(renderScan(res));
    S.photos = null;
  } catch (e) {
    out.replaceChildren(errorBox(e));
  }
}
document.querySelectorAll(".scan-photo").forEach((input) => input.addEventListener("change", scanPhoto));

// A suggestion as a glyph and a word: ✓ read after a label, ▲ a guess, ◆ from the vision model.
function suggestionBadge(s) {
  if (s.source !== "ocr") return st("none", "◆ vision model");
  return s.sure ? st("ok", "✓ read") : st("warn", "▲ guess");
}

// One field (brand or model): an input holding the best suggestion, and a button for each suggestion.
function scanField(label, name, suggestions) {
  const input = el("input", { name, required: true, autocomplete: "off", value: suggestions[0] ? suggestions[0].value : "" });
  const choices = el("ul", { class: "choices" });
  const none = el("li", {}, st("bad", `✗ no ${label.toLowerCase()} read`), " type it from the photo");
  const add = (list) => {
    if (list.length) none.remove();
    for (const s of list) {
      const b = el("button", { type: "button", class: "small" }, s.value);
      b.addEventListener("click", () => { input.value = s.value; input.focus(); });
      choices.append(el("li", {}, b, " ", suggestionBadge(s), el("span", { class: "muted" }, ` ${s.why}`)));
    }
  };
  if (suggestions.length) add(suggestions); else choices.append(none);
  return { row: el("div", { class: "scan-field" }, el("label", {}, label, " ", input), choices), input, add };
}

function renderScan(res) {
  const sha = res.photo.sha256;
  const rd = res.reading;
  const brand = scanField("Brand", "brand", rd.brands);
  const model = scanField("Model", "model", rd.models);
  const serial = rd.serials[0] ? rd.serials[0].value : "";
  const notes = [];
  if (!res.ocr) notes.push(el("p", {}, st("none", "– no OCR on the server"), " Tesseract isn't installed there, so type the brand and model from the photo."));
  else if (!rd.text) notes.push(el("p", {}, st("warn", "▲ no text found"), " Try again closer, with the plate filling the frame and no glare."));
  const go = el("button", { type: "submit", class: "primary" }, "Use these and find");
  const toHomebox = el("div", { class: "hb-add", "aria-live": "polite" });
  const form = el("form", { class: "scan-form" }, brand.row, model.row,
    serial ? el("p", { class: "muted" }, "Serial: ", el("span", { class: "mono" }, serial)) : null,
    el("div", { class: "actions" }, go));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    go.disabled = true;
    try {
      await api(`/api/photos/${sha}`, { brand: brand.input.value, model: model.input.value, serial });
    } catch { /* the lookup still runs; the photo just keeps no label */ }
    findFor(brand.input.value, model.input.value);
    go.disabled = false;
    // Confirmed: now the appliance can go into Homebox too.
    if (S.status && S.status.homebox) toHomebox.replaceChildren(addOffer(sha, brand.input.value, model.input.value, serial));
  });
  const extra = [];
  if (res.vision) {
    const ask = el("button", { type: "button", class: "small" }, `Ask the vision model (${res.vision})`);
    const said = el("span", { "aria-live": "polite" });
    ask.addEventListener("click", async () => {
      ask.disabled = true;
      said.replaceChildren(busy("Asking…"));
      try {
        const v = await api(`/api/photos/${sha}/vision`, {});
        brand.add(v.brands);
        model.add(v.models);
        if (!brand.input.value && v.brands[0]) brand.input.value = v.brands[0].value;
        if (!model.input.value && v.models[0]) model.input.value = v.models[0].value;
        said.replaceChildren(v.brands.length || v.models.length ? st("ok", "✓ answered") : st("warn", "▲ it read nothing"));
      } catch (e) {
        said.replaceChildren(st("bad", `✗ ${e.message}`));
        ask.disabled = false;
      }
    });
    const away = S.status && S.status.vision_leaves_network;
    extra.push(el("p", {}, ask, " ", said, away ? el("span", { class: "muted" }, " (sends the photo to the model's provider)") : null));
    // OCR missed the brand or model and the model runs on this network: ask it without waiting for a click.
    // A model that sends the photo away is only ever asked by the button.
    const local = S.status && !S.status.vision_leaves_network;
    if (local && (!brand.input.value || !model.input.value)) setTimeout(() => ask.click(), 0);
  }
  const text = rd.text ? el("details", {}, el("summary", {}, "what OCR read"), el("pre", { class: "page-text" }, rd.text)) : null;
  return el("div", { class: "panel scan-result" },
    el("img", { class: "plate", src: `/api/photos/${sha}/image`, alt: "The rating plate photo" }),
    el("div", {}, ...notes, form, toHomebox, ...extra, text));
}

// --- Add to Homebox: after a scan is confirmed, make a Homebox item with the photo and the manual.
// Three steps, and nothing is written before the last: the offer; a form, with any items that already have this
// maker and model to use instead of making a duplicate; and a review of exactly what will be written.

function addOffer(photo, brand, model, serial) {
  const b = el("button", { type: "button" }, "Add to Homebox…");
  const box = el("div", {}, el("p", {}, b, " ",
    el("span", { class: "muted" }, "Makes an item with this photo and its manual; you check everything before anything is written.")));
  b.addEventListener("click", async () => {
    b.disabled = true;
    box.replaceChildren(busy("Checking Homebox for this model…"));
    const same = S.lastFind && S.lastFind.brand === brand && S.lastFind.model === model;
    try {
      const pv = await api("/api/homebox/new-item/preview",
        { brand, model, serial, photo, titles: same ? S.lastFind.titles : [] });
      box.replaceChildren(addForm(pv, photo, box));
    } catch (e) {
      box.replaceChildren(errorBox(e), addOffer(photo, brand, model, serial));
    }
  });
  return box;
}

function labelled(label, input) {
  return el("label", { class: "block" }, label, input);
}

function addForm(pv, photo, box) {
  const name = el("input", { name: "name", value: pv.name, maxlength: 255, required: true });
  const maker = el("input", { name: "manufacturer", value: pv.brand, maxlength: 255, required: true });
  const model = el("input", { name: "model", value: pv.model, maxlength: 255, required: true });
  const serial = el("input", { name: "serial", value: pv.serial || "", maxlength: 80, class: "mono" });
  const where = el("select", { name: "location" }, el("option", { value: "" }, "– no location"),
    ...pv.locations.map((l) => el("option", { value: l.id }, l.label)));
  const withPhoto = el("input", { type: "checkbox", name: "photo", checked: true });
  const fields = el("fieldset", { class: "hb-fields" }, el("legend", {}, "New item"),
    labelled("Name", name), labelled("Manufacturer", maker), labelled("Model number", model),
    labelled("Serial number", serial), labelled("Location", where));
  // Items already in Homebox with this maker and model: use one (the default) or make a new item anyway.
  let choice = null;
  if (pv.matches.length) {
    const radios = pv.matches.map((m, i) => el("label", { class: "check" },
      el("input", { type: "radio", name: "target", value: m.id, checked: i === 0 }),
      el("span", {}, el("strong", {}, m.name), m.location ? ` in ${m.location}` : "", " · ",
        m.manuals ? st("ok", `✓ ${m.manuals} manual${m.manuals === 1 ? "" : "s"}`) : st("none", "– no manual"),
        m.serial ? el("span", { class: "muted" }, ` · serial ${m.serial}`) : null, " ", outLink(m.url, "open ↗"))));
    radios.push(el("label", { class: "check" }, el("input", { type: "radio", name: "target", value: "" }),
      "Make a new item anyway"));
    const n = pv.matches.length;
    choice = el("fieldset", { class: "hb-match" },
      el("legend", {}, st("warn", `▲ already in Homebox: ${n} item${n === 1 ? "" : "s"} with this maker and model`)),
      ...radios);
  }
  const target = () => (choice ? choice.querySelector("input[name=target]:checked").value : "");
  const sync = () => { fields.disabled = !!target(); };
  if (choice) choice.addEventListener("change", sync);
  sync();
  const review = el("button", { type: "submit", class: "primary" }, "Review…");
  const cancel = el("button", { type: "button" }, "Cancel");
  const form = el("form", { class: "panel hb-form" }, el("h3", {}, "Add to Homebox"), choice, fields,
    el("label", { class: "check" }, withPhoto, "Attach the rating-plate photo"),
    el("p", { class: "actions" }, review, " ", cancel));
  cancel.addEventListener("click", () => box.replaceChildren(addOffer(photo, pv.brand, pv.model, pv.serial)));
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const existing = target() ? pv.matches.find((m) => m.id === target()) : null;
    const req = {
      confirm: true, photo: withPhoto.checked ? photo : "",
      brand: existing ? existing.manufacturer : maker.value.trim(), model: existing ? existing.model : model.value.trim(),
      name: existing ? "" : name.value.trim(), serial: existing ? "" : serial.value.trim(),
      location_id: existing ? "" : where.value, existing_id: existing ? existing.id : "",
    };
    const place = where.value ? `in ${where.selectedOptions[0].textContent}` : "no location";
    form.replaceWith(addReview(req, existing, place, pv.attach_links, form));
  });
  return form;
}

// The confirm step: every write, listed, before any is made.
function addReview(req, existing, place, links, form) {
  const steps = el("ul", { class: "hb-steps" },
    existing
      ? el("li", {}, "● Use the existing item ", el("strong", {}, existing.name), " (its own fields stay as they are)")
      : el("li", {}, "✎ Create the item ", el("strong", {}, req.name), `: ${req.brand} ${req.model}`,
        req.serial ? `, serial ${req.serial}` : "", `, ${place}`),
    req.photo ? el("li", {}, "▣ Attach the rating-plate photo", existing ? "" : " as its picture") : null,
    el("li", {}, "▤ Find its manual and attach it: a PDF whose text names the model is uploaded; otherwise ",
      links ? "the maker's link is attached" : "nothing (links are off: [homebox] attach_links)",
      existing && existing.manuals ? el("span", { class: "muted" }, " (skipped: it has a manual already)") : null));
  const write = el("button", { type: "button", class: "primary" }, "Write to Homebox");
  const back = el("button", { type: "button" }, "Back");
  const out = el("div", { "aria-live": "polite" });
  const panel = el("div", { class: "panel hb-form" }, el("h3", {}, "Check before writing"),
    el("p", {}, "Manual Beagle will write this to Homebox:"), steps,
    el("p", { class: "actions" }, write, " ", back), out);
  back.addEventListener("click", () => panel.replaceWith(form));
  write.addEventListener("click", async () => {
    write.disabled = true;
    back.disabled = true;
    out.replaceChildren(busy("Writing to Homebox…"));
    try {
      const job = await api("/api/homebox/new-item", req);
      await follow(job.id, out);
    } catch (e) {
      out.replaceChildren(errorBox(e));
      write.disabled = false;
      back.disabled = false;
    }
  });
  return panel;
}

// Fill Find with a confirmed brand and model, and run it.
function findFor(brandName, modelName) {
  const f = $("#find-form");
  f.brand.value = brandName;
  f.model.value = modelName;
  if (location.hash !== "#find") location.hash = "find";
  f.requestSubmit();
}

// --- Library

async function loadDocs(force = false) {
  if (S.docs && !force) return S.docs;
  S.docs = await api("/api/documents");
  S.brands = [...new Set(S.docs.map((d) => d.brand).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  const sel = $("#search-form [name=brand]");
  const keep = sel.value;
  sel.replaceChildren(el("option", { value: "" }, "all"), ...S.brands.map((b) => el("option", { value: b }, b)));
  sel.value = keep;
  return S.docs;
}

async function showLibrary() {
  showPhotos();
  showPaperless();
  const out = $("#docs-out");
  if (!S.docs) out.replaceChildren(busy("Loading the library…"));
  try {
    await loadDocs();
    renderDocs();
  } catch (e) {
    out.replaceChildren(errorBox(e));
  }
}

function renderDocs() {
  const words = $("#docs-filter").value.toLowerCase().split(/\s+/).filter(Boolean);
  const docs = S.docs.filter((d) => {
    const hay = `${d.brand} ${d.model} ${d.title} ${kindLabel(d.kind)}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
  $("#docs-heading").textContent = `Documents (${docs.length}${docs.length === S.docs.length ? "" : ` of ${S.docs.length}`})`;
  if (!S.docs.length) {
    $("#docs-out").replaceChildren(el("p", { class: "muted" }, "– the library is empty: find a manual and fetch it."));
    return;
  }
  const rows = docs.map((d) => el("tr", {},
    el("td", {}, d.brand), el("td", {}, d.model), el("td", {}, kindLabel(d.kind)),
    el("td", { class: "num" }, d.pages), el("td", {}, d.title || "–"),
    el("td", {}, el("span", { class: "links" },
      el("a", { href: `#read/${d.sha256}/1` }, "Read"),
      el("a", { href: `/api/documents/${d.sha256}/pdf`, target: "_blank", rel: "noopener" }, "PDF ↗"),
      outLink(d.source_url, "Source ↗")))));
  $("#docs-out").replaceChildren(el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...["Brand", "Model", "Kind", "Pages", "Title", ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows))));
}

$("#docs-filter").addEventListener("input", () => S.docs && renderDocs());

// Nameplate photos kept in the library, newest first.
async function showPhotos() {
  try {
    if (!S.photos) S.photos = await api("/api/photos");
  } catch (e) {
    $("#photos-out").replaceChildren(errorBox(e));
    return;
  }
  $("#photos-heading").hidden = !S.photos.length;
  $("#photos-heading").textContent = `Nameplate photos (${S.photos.length})`;
  const items = S.photos.map((p) => {
    const named = p.brand && p.model;
    const find = el("button", { type: "button", class: "small", disabled: !named }, "Find →");
    find.addEventListener("click", () => findFor(p.brand, p.model));
    const del = el("button", { type: "button", class: "small" }, "Delete");
    del.addEventListener("click", async () => {
      if (!confirm("Delete this photo from the library?")) return;
      try {
        await api(`/api/photos/${p.sha256}/delete`, {});
        S.photos = null;
        showPhotos();
      } catch (e) {
        $("#photos-out").prepend(errorBox(e));
      }
    });
    return el("li", { class: "photo" },
      el("a", { href: `/api/photos/${p.sha256}/image`, target: "_blank", rel: "noopener" },
        el("img", { src: `/api/photos/${p.sha256}/image`, alt: named ? `Rating plate of ${p.brand} ${p.model}` : "A rating plate", loading: "lazy" })),
      el("div", {},
        named ? el("strong", {}, `${p.brand} ${p.model}`) : st("none", "– not confirmed yet"),
        p.serial ? el("div", { class: "muted mono" }, `S/N ${p.serial}`) : null,
        el("div", { class: "muted" }, new Date(p.taken_at * 1000).toLocaleDateString()),
        el("div", { class: "links" }, find, del)));
  });
  $("#photos-out").replaceChildren(items.length ? el("ul", { class: "photos" }, items) : "");
}

// A snippet with \x02…\x03 around matches, as text nodes and <mark>s.
function marked(snip) {
  const out = [];
  const parts = snip.split(/\x02|\x03/);
  parts.forEach((p, i) => out.push(i % 2 ? el("mark", {}, p) : p));
  return out;
}

$("#search-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const q = f.q.value.trim();
  const out = $("#search-out");
  if (!q) { out.replaceChildren(); return; }
  out.replaceChildren(busy("Searching…"));
  try {
    const p = new URLSearchParams({ q });
    if (f.brand.value) p.set("brand", f.brand.value);
    const res = await api(`/api/search?${p}`);
    if (!res.rows.length) {
      out.replaceChildren(el("p", {}, st("none", "– no matches"), res.plain ? " (searched for the words as typed)" : ""));
      return;
    }
    const list = el("ul", { class: "hits panel" });
    for (const r of res.rows) {
      const href = `#read/${r.sha256}/${r.page}?q=${encodeURIComponent(q)}`;
      list.append(el("li", { class: "hit" },
        el("a", { href }, `${r.brand} ${r.model} · ${r.title || kindLabel(r.kind)} · p. ${r.page}`),
        el("p", { class: "snip" }, marked(r.snip.replace(/\s+/g, " ")))));
    }
    out.replaceChildren(
      el("p", { class: "muted" }, `${res.rows.length} pages`, res.plain ? " (searched for the words as typed)" : ""),
      list);
  } catch (e) {
    out.replaceChildren(errorBox(e));
  }
});

// --- Ask: a question answered from the library's pages; each [n] in the answer opens the Reader at that page.

function askHint() {
  const s = S.status;
  if (!s) return "";
  if (!s.vision) return "– No AI model is set up (Settings), so you get the best pages to read, not a written answer.";
  if (s.vision_leaves_network) {
    return `▲ Answers are written by ${s.vision}: your question and the text of the pages it reads are sent to its maker.`;
  }
  return `✓ Answers are written by ${s.vision} on your own network, from the library's pages only.`;
}

function fillAskPickers() {
  const f = $("#ask-form");
  const keepBrand = f.brand.value;
  const keepDoc = f.document.value;
  f.brand.replaceChildren(el("option", { value: "" }, "all"), ...S.brands.map((b) => el("option", { value: b }, b)));
  f.brand.value = S.brands.includes(keepBrand) ? keepBrand : "";
  const docs = S.docs.filter((d) => !f.brand.value || d.brand === f.brand.value);
  const name = (d) => {
    const t = d.title || kindLabel(d.kind);
    return `${d.brand} ${d.model} · ${t.length > 50 ? `${t.slice(0, 49)}…` : t}`;
  };
  f.document.replaceChildren(el("option", { value: "" }, "all manuals"),
    ...docs.map((d) => el("option", { value: d.sha256 }, name(d))));
  f.document.value = docs.some((d) => d.sha256 === keepDoc) ? keepDoc : "";
}

async function showAsk() {
  if (!S.status) await loadStatus();
  $("#ask-hint").textContent = askHint();
  try {
    await loadDocs();
    fillAskPickers();
  } catch (e) {
    $("#ask-out").replaceChildren(errorBox(e));
  }
  $("#ask-form [name=question]").focus({ preventScroll: true });
}

$("#ask-form [name=brand]").addEventListener("change", fillAskPickers);

$("#ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const question = f.question.value.trim();
  if (!question) return;
  const out = $("#ask-out");
  const btn = f.querySelector("button[type=submit]");
  btn.disabled = true;
  const model = S.status && S.status.vision;
  out.replaceChildren(busy(model ? `Reading the manuals and asking ${model}…` : "Searching the manuals…"));
  try {
    const body = { question };
    if (f.brand.value) body.brand = f.brand.value;
    if (f.document.value) body.document = f.document.value;
    out.replaceChildren(...renderAnswer(await api("/api/ask", body)));
  } catch (e) {
    out.replaceChildren(errorBox(e));
  } finally {
    btn.disabled = false;
  }
});

function renderAnswer(a) {
  const q = a.terms.join(" ");
  const href = (s) => `#read/${s.sha256}/${s.page}?q=${encodeURIComponent(q)}`;
  const byN = Object.fromEntries(a.sources.map((s) => [String(s.n), s]));
  const cited = a.sources.some((s) => s.cited);
  const kind = {
    answered: cited ? "ok" : "warn", not_found: "none", no_pages: "none", pages_only: "warn", model_error: "bad",
  }[a.status] || "none";
  const parts = [el("p", {}, st(kind, a.headline), a.model ? el("span", { class: "muted" }, ` · ${a.model}`) : null)];
  if (a.answer) {
    const box = el("div", { class: "answer panel" });
    for (const para of a.answer.split(/\n{2,}/)) {
      const line = el("p");
      para.split(/\[(\d+)\]/).forEach((bit, i) => {
        const s = i % 2 ? byN[bit] : null;
        if (!(i % 2)) line.append(bit);
        else line.append(s ? el("a", { href: href(s), class: "cite", title: s.label }, `[${bit}]`) : `[${bit}]`);
      });
      box.append(line);
    }
    parts.push(box);
  }
  if (a.sources.length) {
    parts.push(el("h2", {}, a.status === "answered" ? "Pages it read" : "The best pages"));
    const list = el("ul", { class: "hits panel" });
    for (const s of a.sources) {
      const tag = a.status !== "answered" ? null : s.cited ? st("ok", "✓ cited") : st("none", "○ read, not cited");
      list.append(el("li", { class: "hit" },
        el("a", { href: href(s) }, `[${s.n}] ${s.brand} ${s.model} · ${s.title || kindLabel(s.kind)} · p. ${s.page}`),
        tag ? [" ", tag] : null,
        el("p", { class: "snip" }, marked(s.snip))));
    }
    parts.push(list);
  }
  if (a.leaves_network) parts.push(el("p", { class: "hint" }, `▲ The question and these pages' text went to ${a.model}.`));
  return parts;
}

// --- Reader

// The words of a search, for highlighting a page: quotes, operators and * stripped.
function terms(q) {
  return q.replace(/["()*^:]/g, " ").split(/\s+/)
    .filter((w) => w && !["AND", "OR", "NOT", "NEAR"].includes(w)).map((w) => w.toLowerCase());
}

function highlight(text, words) {
  if (!words.length) return [text];
  const esc = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`\\b(${esc.join("|")})\\w*`, "gi");
  const out = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    out.push(text.slice(last, m.index), el("mark", {}, m[0]));
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

async function showReader(sha, page, q) {
  const out = $("#read-out");
  out.replaceChildren(busy("Loading…"));
  try {
    const [doc, pages] = await Promise.all([
      api(`/api/documents/${sha}`),
      api(`/api/documents/${sha}/pages?start=${page}&count=1`),
    ]);
    const n = pages.page_count;
    const text = pages.pages[String(page)];
    const qs = q ? `?q=${encodeURIComponent(q)}` : "";
    const go = (p) => { location.hash = `read/${sha}/${Math.min(Math.max(p, 1), n)}${qs}`; };
    const jump = el("input", { type: "number", min: 1, max: n, value: page, "aria-label": "Page" });
    jump.addEventListener("change", () => go(parseInt(jump.value, 10) || 1));
    const prev = el("button", { type: "button", disabled: page <= 1 }, "‹ Previous");
    prev.addEventListener("click", () => go(page - 1));
    const next = el("button", { type: "button", disabled: page >= n }, "Next ›");
    next.addEventListener("click", () => go(page + 1));
    const body = text === undefined
      ? el("p", {}, st("bad", `✗ page ${page} isn't in this document`))
      : text.trim().length < 20
        ? el("p", { class: "page-text" }, st("warn", "▲ no text on this page"),
          " It may be a drawing or a scan; with OCR installed, run manualbeagle ocr.")
        : el("pre", { class: "page-text" }, highlight(text, terms(q)));
    out.replaceChildren(
      el("p", {}, el("a", { href: "#library" }, "← Library")),
      el("div", { class: "reader-head" },
        el("h1", {}, `${doc.brand} ${doc.model}`),
        el("span", { class: "muted" }, `${doc.title || kindLabel(doc.kind)} · ${kindLabel(doc.kind)} · ${n} pages${doc.ocr ? " · OCR" : ""}`)),
      el("div", { class: "pager" }, prev, el("label", { class: "check" }, "Page ", jump, ` of ${n}`), next,
        el("a", { href: `/api/documents/${sha}/pdf#page=${page}`, target: "_blank", rel: "noopener" }, "Open the PDF at this page ↗"),
        outLink(doc.source_url, "Source ↗"), paperlessButton(sha)),
      body);
    document.onkeydown = (ev) => {
      if ($("#view-read").hidden || ev.target.matches("input, textarea, select")) return;
      if (ev.key === "ArrowLeft" && page > 1) go(page - 1);
      if (ev.key === "ArrowRight" && page < n) go(page + 1);
    };
  } catch (e) {
    out.replaceChildren(el("p", {}, el("a", { href: "#library" }, "← Library")), errorBox(e));
  }
}

// --- Jobs (batch, sync, attach): start, then poll until done.

const BATCH = {
  found: ["ok", "✓ found"], link: ["warn", "▲ link"], weak: ["warn", "▲ weak"],
  none: ["bad", "✗ none"], nosearch: ["bad", "! search failed"], skip: ["none", "– skip"],
};
const PLAN = { upload: ["ok", "✓ manual"], link: ["warn", "▲ link"], skip: ["none", "– skip"], none: ["bad", "✗ none"] };
const STATE = {
  running: ["none", "… running"], done: ["ok", "✓ done"], failed: ["bad", "✗ failed"], cancelled: ["warn", "▲ cancelled"],
};

function batchRow(r) {
  const [k, w] = BATCH[r.result] || ["none", r.result];
  const best = (r.top || [])[0];
  return el("tr", {}, el("td", {}, st(k, w)), el("td", {}, r.name), el("td", {}, `${r.brand} ${r.model}`),
    el("td", {}, best
      ? [el("span", { class: "mono" }, `${Math.round(best.score)} `), `${kindLabel(best.kind)}: `, outLink(best.url, best.title || best.url)]
      : r.result === "skip" ? "no model number" : (r.search_error ? `web search failed: ${r.search_error}` : "–")));
}

function planRow(r, attach) {
  let state;
  if (attach) state = r.done ? st("ok", "✓ attached") : st("bad", `✗ refused: ${r.error || "?"}`);
  else { const [k, w] = PLAN[r.action] || ["none", r.action]; state = st(k, w); }
  return el("tr", {}, el("td", {}, state), el("td", {}, r.item), el("td", {}, `${r.brand} ${r.model}`),
    el("td", {}, r.reason || "–",
      r.url ? el("div", {}, outLink(r.url, r.title || r.url)) : null,
      r.document ? el("div", {}, el("a", { href: `#read/${r.document}/1` }, "Read →")) : null));
}

// A step of Add to Homebox: what was written, with a link to the item or the manual.
const STEP = { item: "Item", serial: "Serial", photo: "Photo", manual: "Manual" };
const STEP_STATE = { ok: ["ok", "✓ done"], skip: ["none", "– skipped"], bad: ["bad", "✗ failed"] };

function addRow(r) {
  const [k, w] = STEP_STATE[r.state] || ["none", r.state];
  const link = r.url ? (r.step === "item" ? outLink(r.url, "Open in Homebox ↗") : outLink(r.url)) : null;
  return el("tr", {}, el("td", {}, st(k, w)), el("td", {}, STEP[r.step] || r.step),
    el("td", {}, r.text || "–", link ? el("div", {}, link) : null,
      r.document ? el("div", {}, el("a", { href: `#read/${r.document}/1` }, "Read →")) : null));
}

function renderJob(job, out, after) {
  const [k, w] = STATE[job.state];
  const pct = job.total ? Math.round((100 * job.done) / job.total) : 100;
  const fill = el("span");
  fill.style.width = `${pct}%`; // a CSSOM property, which the page's CSP allows (an inline style attribute isn't)
  const bar = el("div", { class: "bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": job.total, "aria-valuenow": job.done },
    fill);
  const head = el("div", { class: "progress" }, st(k, w), el("span", {}, `${job.done} of ${job.total}`), bar);
  if (job.state === "running") {
    const c = el("button", { type: "button", class: "small" }, "Cancel");
    c.addEventListener("click", async () => { c.disabled = true; try { await api(`/api/jobs/${job.id}/cancel`, {}); } catch { /* shown on next poll */ } });
    head.append(c);
  }
  const plan = job.kind !== "batch" || job.fetch;
  const add = job.kind === "add";
  const maint = job.kind === "maintenance";
  const rows = job.rows.map((r) => (add ? addRow(r) : maint ? maintJobRow(r)
    : job.kind === "paperless" ? paperlessRow(r) : plan ? planRow(r, job.kind === "attach") : batchRow(r)));
  const heads = add ? ["Result", "Step", "What"]
    : maint ? ["Result", "Document", "Found", "Homebox item"] : ["Result", "Name", "Brand and model", "What"];
  const table = el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...heads.map((h) => el("th", {}, h)))),
    el("tbody", {}, rows)));
  out.replaceChildren(el("div", { class: "panel" }, head,
    job.error ? el("p", { class: "error" }, `✗ ${job.error}`) : null,
    job.warning ? el("p", {}, st("warn", "▲"), ` ${job.warning}`) : null,
    job.summary ? el("pre", { class: "summary" }, job.summary) : null,
    after ? after(job) : null), table);
}

async function follow(jobId, out, after) {
  for (;;) {
    let job;
    try { job = await api(`/api/jobs/${jobId}`); } catch (e) { out.prepend(errorBox(e)); return null; }
    renderJob(job, out, after);
    if (job.state !== "running") { loadStatus(); S.docs = null; return job; }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

// --- Batch

$("#batch-form [name=file]").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const f = $("#batch-form");
  f.text.value = await file.text();
  const ext = file.name.toLowerCase().split(".").pop();
  f.format.value = ext === "csv" ? "csv" : (ext === "yaml" || ext === "yml") ? "yaml" : "auto";
});

$("#batch-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const out = $("#batch-out");
  const btn = f.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const job = await api("/api/batch", {
      text: f.text.value, format: f.format.value, fetch: f.fetch.checked, min_score: f.min_score.value,
    });
    await follow(job.id, out);
  } catch (e) {
    out.replaceChildren(errorBox(e));
  } finally {
    btn.disabled = false;
  }
});

// --- Homebox

async function showHomebox() {
  showHomeAssistant();
  const out = $("#homebox-status");
  out.replaceChildren(busy("Asking Homebox…"));
  try {
    const h = await api("/api/homebox");
    if (h.ok) out.replaceChildren(el("p", {}, st("ok", "✓ connected"), ` Homebox ${h.version} at `, outLink(h.url)));
    else if (!h.configured) out.replaceChildren(el("p", {}, st("warn", "▲ not set up"), ` ${h.error}. `,
      "Set its address on ", el("a", { href: "#settings" }, "Settings"),
      "; its API key goes in the config file or the server's environment."));
    else out.replaceChildren(el("p", {}, st("bad", "✗ not answering"), ` ${h.url}: ${h.error}`));
  } catch (e) {
    out.replaceChildren(errorBox(e));
  }
}

function attachButton(job) {
  if (job.kind !== "sync" || job.state !== "done") return null;
  const n = job.rows.filter((r) => r.action === "upload" || r.action === "link").length;
  if (job.attached) return el("p", { class: "muted" }, "These plans were attached; run a new dry run to sync again.");
  if (!n) return el("p", { class: "muted" }, "– nothing to attach.");
  const b = el("button", { type: "button", class: "primary" }, `Attach ${n} to Homebox…`);
  b.addEventListener("click", async () => {
    const ups = job.rows.filter((r) => r.action === "upload").length;
    if (!confirm(`Write to Homebox: upload ${ups} PDF${ups === 1 ? "" : "s"} and attach ${n - ups} link${n - ups === 1 ? "" : "s"}?`)) return;
    b.disabled = true;
    try {
      const a = await api(`/api/jobs/${job.id}/attach`, {});
      await follow(a.id, $("#sync-out"));
    } catch (e) {
      $("#sync-out").prepend(errorBox(e));
      b.disabled = false;
    }
  });
  return el("p", {}, b);
}

$("#sync-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const out = $("#sync-out");
  const btn = f.querySelector("button[type=submit]");
  btn.disabled = true;
  out.replaceChildren(busy("Reading Homebox's items…"));
  try {
    const job = await api("/api/sync", {
      item: f.item.value, min_score: f.min_score.value, force: f.force.checked,
      links_only: f.links_only.checked, allow_search_links: f.allow_search_links.checked,
    });
    await follow(job.id, out, attachButton);
  } catch (e) {
    out.replaceChildren(errorBox(e));
  } finally {
    btn.disabled = false;
  }
});

// --- Home Assistant: read its devices, review, then add the new ones to Homebox (each, or all after a confirm).

const OFFER = { new: ["ok", "+ new"], have: ["none", "✓ in Homebox"], skip: ["none", "– skip"] };
let HA = null;

function offerRow(o, picked) {
  const [k, w] = OFFER[o.action] || ["none", o.action];
  const box = o.action === "new"
    ? el("input", { type: "checkbox", checked: picked.has(o.device_id), "aria-label": `Add ${o.name}` })
    : null;
  if (box) box.addEventListener("change", () => { if (box.checked) picked.add(o.device_id); else picked.delete(o.device_id); renderOffers(); });
  const add = o.action === "new" ? el("button", { type: "button", class: "small" }, "Add…") : null;
  if (add) add.addEventListener("click", () => importDevices([o]));
  return el("tr", {}, el("td", {}, box), el("td", {}, st(k, w)), el("td", {}, o.name || "–"),
    el("td", {}, `${o.brand} ${o.model}`.trim() || "–"), el("td", {}, o.location || "–"),
    el("td", {}, o.reason, o.same_model.length ? el("div", { class: "muted" }, `also: ${o.same_model.join(", ")}`) : null),
    el("td", {}, add));
}

function renderOffers() {
  const out = $("#ha-out");
  if (!HA) return;
  const showSkipped = $("#ha-skipped").checked;
  const shown = HA.offers.filter((o) => showSkipped || o.action !== "skip");
  const fresh = HA.offers.filter((o) => o.action === "new");
  const chosen = fresh.filter((o) => HA.picked.has(o.device_id));
  const addAll = el("button", { type: "button", class: "primary", disabled: !chosen.length },
    `Add ${chosen.length} to Homebox…`);
  addAll.addEventListener("click", () => importDevices(chosen));
  const find = el("button", { type: "button", disabled: !HA.offers.some((o) => o.action !== "skip") },
    "Look up their manuals");
  find.addEventListener("click", findDeviceManuals);
  out.replaceChildren(
    el("div", { class: "panel" },
      el("pre", { class: "summary" }, HA.summary),
      HA.homebox ? null : el("p", {}, st("warn", "▲ Homebox didn't answer"), ` ${HA.homebox_error}: every device counts as new, and nothing can be added.`),
      el("p", { class: "row-form" }, addAll, find)),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ...["", "Result", "Device", "Brand and model", "Area", "Why", ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, shown.map((o) => offerRow(o, HA.picked))))));
}

async function readDevices() {
  const out = $("#ha-out");
  out.replaceChildren(busy("Reading Home Assistant's devices and Homebox's items…"));
  try {
    const res = await api("/api/homeassistant/devices");
    HA = { ...res, picked: new Set(res.offers.filter((o) => o.action === "new").map((o) => o.device_id)) };
    renderOffers();
  } catch (e) {
    out.replaceChildren(errorBox(e));
  }
}

async function importDevices(offers) {
  if (!offers.length) return;
  const what = offers.length === 1 ? `"${offers[0].name}" (${offers[0].brand} ${offers[0].model})` : `${offers.length} items`;
  if (!confirm(`Add ${what} to Homebox, each in its area?`)) return;
  const out = $("#ha-out");
  out.prepend(busy("Adding to Homebox…"));
  try {
    const res = await api("/api/homeassistant/import", { device_ids: offers.map((o) => o.device_id) });
    await readDevices();
    $("#ha-out").prepend(el("p", {}, res.summary, ". ", "Sync above finds their manuals."),
      ...res.rows.filter((r) => !r.done).map((r) => el("p", { class: "error" }, `✗ ${r.name}: ${r.error}`)));
  } catch (e) {
    out.prepend(errorBox(e));
  }
}

async function findDeviceManuals() {
  const rows = HA.offers.filter((o) => o.action !== "skip").map((o) => ({ name: o.name, brand: o.brand, model: o.model }));
  const out = $("#ha-find-out");
  try {
    // JSON is YAML, so the batch endpoint reads the list as it is.
    const job = await api("/api/batch", { text: JSON.stringify(rows), format: "yaml", fetch: false });
    await follow(job.id, out);
  } catch (e) {
    out.replaceChildren(errorBox(e));
  }
}

$("#ha-read").addEventListener("click", readDevices);
$("#ha-skipped").addEventListener("change", renderOffers);

async function showHomeAssistant() {
  if (!S.status) await loadStatus();
  const on = !!(S.status && S.status.home_assistant);
  $("#ha-read").disabled = !on;
  if (!on && !HA) {
    $("#ha-out").replaceChildren(el("p", {}, st("none", "– not set up"),
      " Set Home Assistant's address on ", el("a", { href: "#settings" }, "Settings"),
      "; its token goes in the config file or the server's environment."));
  }
}

// --- Paperless-ngx: file manuals there, one from the reader or every one not sent yet.

const FILED = {
  filed: ["ok", "✓ filed"], exists: ["none", "= there already"], queued: ["warn", "… queued"], failed: ["bad", "✗ failed"],
};

function paperlessRow(r) {
  const [k, w] = FILED[r.state] || ["none", r.state];
  return el("tr", {}, el("td", {}, st(k, w)), el("td", {}, r.title || r.sha256.slice(0, 12)), el("td", {}, ""),
    el("td", {}, r.message || "–"));
}

async function showPaperless() {
  if (!S.status) await loadStatus();
  const out = $("#paperless-out");
  if (!S.status || !S.status.paperless) { out.replaceChildren(); return; }
  try {
    const p = await api("/api/paperless");
    if (!p.ok) {
      out.replaceChildren(el("p", {}, st("bad", "✗ Paperless-ngx"), ` ${p.error}`));
      return;
    }
    const c = p.counts || {};
    const send = el("button", { type: "button", class: "small", disabled: !p.unsent }, `Send ${p.unsent} to Paperless…`);
    send.addEventListener("click", async () => {
      if (!confirm(`File ${p.unsent} manual${p.unsent === 1 ? "" : "s"} in Paperless-ngx, tagged with brand and model?`)) return;
      send.disabled = true;
      try {
        const job = await api("/api/paperless/send", { all: true });
        await follow(job.id, $("#paperless-out"));
      } catch (e) {
        out.append(errorBox(e));
      }
    });
    out.replaceChildren(el("p", {}, st("ok", "✓ Paperless-ngx"), ` ${p.version}: `,
      `${c.filed || 0} filed, ${c.exists || 0} there already, ${c.queued || 0} queued, ${c.failed || 0} failed, ${p.unsent} not sent. `,
      p.auto ? st("ok", "✓ new manuals are filed by themselves") : send));
  } catch (e) {
    out.replaceChildren(errorBox(e));
  }
}

function paperlessButton(sha) {
  if (!S.status || !S.status.paperless) return null;
  const b = el("button", { type: "button", class: "small" }, "Send to Paperless…");
  const out = el("span", { class: "hint" });
  b.addEventListener("click", async () => {
    if (!confirm("File this manual in Paperless-ngx? It isn't sent if Paperless has this file already.")) return;
    b.disabled = true;
    out.replaceChildren(" sending…");
    try {
      const job = await api("/api/paperless/send", { sha256: sha });
      const done = await follow(job.id, el("div"));
      const r = done && done.rows[0];
      const [k, w] = r ? FILED[r.state] || ["none", r.state] : ["bad", "✗ failed"];
      out.replaceChildren(" ", st(k, w), r && r.message ? `: ${r.message}` : "");
    } catch (e) {
      out.replaceChildren(" ", st("bad", `✗ ${e.message}`));
    } finally {
      b.disabled = false;
    }
  });
  return el("span", {}, b, out);
}

// --- Maintenance: suggestions read from the manuals, each approved (filed in Homebox or Donetick) or dismissed.

const MAINT_STATE = {
  new: ["warn", "● waiting"], filed: ["ok", "✓ filed"], dismissed: ["none", "– dismissed"],
};
const TARGET_NAME = { homebox: "Homebox", donetick: "Donetick" };
// `target` is where an approval goes ([maintenance] target); `ready` says it has an address.
const M = { items: null, ai: null, homebox: false, target: "homebox", ready: false };

function maintJobRow(r) {
  const state = r.error ? st("warn", "▲ model failed") : r.found ? st("ok", `✓ ${r.added} new`) : st("none", "– none");
  return el("tr", {}, el("td", {}, state),
    el("td", {}, `${r.brand} ${r.model}`, r.title ? el("div", { class: "muted" }, r.title) : null),
    el("td", {}, r.found ? `${r.found} task${r.found === 1 ? "" : "s"}` : "–",
      r.pages_asked ? el("div", { class: "muted" }, `${r.pages_asked} pages asked`) : null,
      r.error ? el("div", { class: "muted" }, r.error) : null),
    el("td", {}, r.items.length ? r.items.join(", ") : el("span", { class: "muted" }, "no matching item")));
}

function sourceText(src) {
  if (src === "rules") return "found by the rules";
  if (src.startsWith("rules+")) return `found by the rules, worded by ${src.slice(6)}`;
  return `found by ${src}`;
}

async function homeboxItems() {
  if (M.items === null) M.items = await api("/api/maintenance/items");
  return M.items;
}

function suggestionCard(s) {
  let [k, w] = MAINT_STATE[s.status] || ["none", s.status];
  if (s.status === "filed") w = `✓ in ${TARGET_NAME[s.filed_in] || s.filed_in}`;
  const where = TARGET_NAME[M.target] || M.target;
  const card = el("div", { class: "panel maint" });
  const quote = el("blockquote", { class: "quote" }, `“${s.quote}”`,
    el("div", { class: "muted" }, "— ", s.title || `${s.brand} ${s.model}`, ", ",
      el("a", { href: `#read/${s.sha256}/${s.page}` }, `page ${s.page} →`)));
  const meta = el("p", { class: "muted fine" }, sourceText(s.source));
  if (s.status !== "new") {
    const what = s.status === "filed"
      ? [s.filed_in === "donetick" ? `, first due ${s.scheduled}` : ` for ${s.scheduled}`,
        s.item_name ? ` ${s.filed_in === "donetick" ? "for" : "on"} ${s.item_name}` : ""]
      : [];
    card.append(el("p", { class: "maint-head" }, st(k, w), " ", el("b", {}, s.task), `, ${s.every}`, ...what), quote, meta);
    if (s.status === "dismissed") {
      const undo = el("button", { type: "button", class: "small" }, "Undo");
      undo.addEventListener("click", () => decide(s, "reopen", undo));
      card.append(undo);
    }
    return card;
  }
  const form = el("form", { class: "row-form" });
  const task = el("input", { name: "task", value: s.task, maxlength: 255, required: true });
  const date = el("input", { name: "scheduled", type: "date", value: s.first_date, required: true });
  form.append(el("label", { class: "grow" }, "Task", task), el("label", {}, "First date", date));
  let itemSel = null;
  let appliance = null;
  if (M.target === "donetick") {
    // The chore is named "<appliance>: <task>"; no Homebox item is needed.
    appliance = el("input", { name: "appliance", value: s.item_name || `${s.brand} ${s.model}`, maxlength: 120 });
    form.append(el("label", {}, "Appliance", appliance));
  } else if (s.item_id) {
    form.append(el("p", { class: "item-name" }, "Homebox item: ", el("b", {}, s.item_name)));
  } else if (M.homebox) {
    itemSel = el("select", { name: "item_id", required: true }, el("option", { value: "" }, "choose the item…"));
    homeboxItems().then((items) => {
      for (const i of items) itemSel.append(el("option", { value: i.id }, i.name));
    }).catch((e) => card.append(errorBox(e)));
    form.append(el("label", {}, `Homebox item (${s.brand} ${s.model})`, itemSel));
  }
  const add = el("button", { type: "submit", class: "primary", disabled: !M.ready }, `✓ Add to ${where}`);
  const dismiss = el("button", { type: "button" }, "✗ Dismiss");
  dismiss.addEventListener("click", () => decide(s, "dismiss", dismiss));
  form.append(add, dismiss);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    add.disabled = true;
    try {
      const done = await api(`/api/maintenance/${s.id}/approve`, {
        task: task.value, scheduled: date.value, item_id: itemSel ? itemSel.value : "",
        appliance: appliance ? appliance.value : "",
      });
      card.replaceWith(suggestionCard(done));
      loadMaintenance(false);
    } catch (e) {
      card.append(errorBox(e));
      add.disabled = false;
    }
  });
  // DOM append prints null as text, so the optional parts are left out first.
  card.append(...[el("p", { class: "maint-head" }, st(k, w), " ", el("b", {}, s.task), `, ${s.every}`), quote, meta,
    s.error ? el("p", {}, st("bad", "✗ last try:"), ` ${s.error}`) : null, form,
    M.ready ? null : el("p", { class: "muted" }, `▲ ${where} isn't set up: set its address on `,
      el("a", { href: "#settings" }, "Settings"), ".")].filter(Boolean));
  return card;
}

async function decide(s, action, btn) {
  btn.disabled = true;
  try {
    await api(`/api/maintenance/${s.id}/${action}`, {});
    loadMaintenance();
  } catch (e) {
    btn.parentElement.append(errorBox(e));
    btn.disabled = false;
  }
}

// Load the suggestions in the chosen state; `redraw` false only refreshes the counts.
async function loadMaintenance(redraw = true) {
  const out = $("#maint-out");
  const status = $("#maint-status").value;
  try {
    const res = await api(`/api/maintenance?status=${encodeURIComponent(status)}`);
    M.ai = res.ai;
    M.homebox = res.homebox;
    M.target = res.target || "homebox";
    M.ready = res.ready;
    $("#maint-ai").hidden = !res.ai;
    $("#maint-ai-name").textContent = res.ai || "";
    const n = res.counts;
    $("#maint-counts").textContent = `● ${n.new} waiting · ✓ ${n.filed} filed · – ${n.dismissed} dismissed`;
    if (!redraw) return;
    if (!res.suggestions.length) {
      out.replaceChildren(el("p", { class: "muted" }, status === "new"
        ? "– nothing waiting. Read the manuals to look for maintenance tasks."
        : "– none."));
      return;
    }
    const groups = new Map();
    for (const s of res.suggestions) {
      const who = s.item_name || `${s.brand} ${s.model}`;
      if (!groups.has(who)) groups.set(who, []);
      groups.get(who).push(s);
    }
    const parts = [];
    for (const [who, list] of groups) {
      const first = list[0];
      parts.push(el("h2", {}, who, el("span", { class: "muted" },
        first.item_name ? ` · ${first.brand} ${first.model}` : " · no Homebox item matched")));
      for (const s of list) parts.push(suggestionCard(s));
    }
    out.replaceChildren(...parts);
  } catch (e) {
    out.replaceChildren(errorBox(e));
  }
}

function showMaintenance() {
  M.items = null;
  loadMaintenance();
}

$("#maint-status").addEventListener("change", () => loadMaintenance());

$("#maint-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const out = $("#maint-job");
  const btn = f.querySelector("button[type=submit]");
  btn.disabled = true;
  out.replaceChildren(busy("Reading Homebox's items…"));
  try {
    const job = await api("/api/maintenance/suggest", { ai: !$("#maint-ai").hidden && f.ai.checked });
    await follow(job.id, out);
    loadMaintenance();
  } catch (e) {
    out.replaceChildren(errorBox(e));
  } finally {
    btn.disabled = false;
  }
});

// --- Settings

// Where a value comes from, as a glyph and a word.
const SOURCES = {
  page: ["warn", "✎ changed here"],
  file: ["ok", "● config file"],
  default: ["none", "○ default"],
  env: ["bad", "▲ set by the server's environment"],
};
const SECTIONS = [
  ["Lookups", ["delay", "respect_robots"]],
  ["Web search", ["search.engine", "search.url"]],
  ["Homebox", ["homebox.url", "homebox.attach_links"]],
  ["Home Assistant", ["home_assistant.url"]],
  ["Paperless-ngx", ["paperless.url", "paperless.auto", "paperless.document_type"]],
  ["Maintenance", ["maintenance.target", "donetick.url", "donetick.label"]],
  ["AI model (rating plates, Ask)", ["ai.provider", "ai.model", "ai.url"]],
];

function settingInput(f) {
  const attrs = { name: f.key, id: `set-${f.key}`, disabled: f.source === "env" };
  if (f.kind === "bool") return el("input", { ...attrs, type: "checkbox", checked: !!f.value });
  if (f.kind === "choice") {
    const s = el("select", attrs, f.choices.map((c) => el("option", { value: c, selected: c === f.value }, c)));
    return s;
  }
  const input = el("input", {
    ...attrs, value: f.value ?? "", autocomplete: "off", spellcheck: "false",
    inputmode: f.kind === "number" ? "decimal" : null, type: f.kind === "url" ? "url" : "text",
    list: f.key === "ai.model" ? "ollama-models" : null,
  });
  return input;
}

function settingRow(f, keys) {
  const [kind, word] = SOURCES[f.source] || SOURCES.default;
  const input = settingInput(f);
  const why = f.source === "env" ? `${word} (${f.env})` : word;
  const row = el("div", { class: "setting" },
    f.kind === "bool"
      ? el("label", { class: "check", for: input.id }, input, f.label)
      : el("label", { for: input.id }, f.label, input),
    el("p", { class: "hint" }, st(kind, why),
      f.source === "page" && f.file_value !== null && f.file_value !== undefined ? ` · the file says ${f.file_value}` : "",
      f.help ? ` · ${f.help}` : ""));
  if (f.source === "page") {
    row.append(el("button", { type: "button", class: "small", onclick: () => saveSettings({ [f.key]: null }) },
      "Use the file's value"));
  }
  if (f.key === "homebox.url") {
    const held = (keys.held_back || {}).homebox;
    row.append(el("p", { class: "hint" }, held ? st("bad", `✗ API key not sent: ${held}`)
      : keys.homebox ? st("ok", "✓ API key found") : st("warn", "▲ no API key")));
  }
  for (const t of ["home_assistant", "paperless", "donetick"]) {
    if (f.key !== `${t}.url` || !f.value) continue;
    const held = (keys.held_back || {})[t];
    row.append(el("p", { class: "hint" }, held ? st("bad", `✗ token not sent: ${held}`)
      : keys[t] ? st("ok", "✓ token found") : st("warn", "▲ no token")));
  }
  if (f.key === "ai.model") {
    const list = el("datalist", { id: "ollama-models" });
    const out = el("span", { class: "hint" });
    const b = el("button", { type: "button", class: "small" }, "List Ollama's models");
    b.addEventListener("click", async () => {
      b.disabled = true;
      out.replaceChildren(" asking…");
      try {
        const r = await api("/api/settings/ollama-models", { url: $("#settings-form").elements["ai.url"].value });
        list.replaceChildren(...r.models.map((m) => el("option", { value: m })));
        out.replaceChildren(" ", r.ok ? st("ok", `✓ ${r.models.length} models`) : st("bad", `✗ ${r.error}`),
          r.ok && r.models.length ? ": pick one in the Model box" : "");
      } catch (e) {
        out.replaceChildren(" ", st("bad", `✗ ${e.message}`));
      } finally {
        b.disabled = false;
      }
    });
    row.append(list, el("p", {}, b, out));
  }
  if (f.key === "ai.provider" && f.value === "anthropic") {
    row.append(el("p", { class: "hint" }, keys.anthropic ? st("ok", "✓ API key found") : st("warn", "▲ no API key")));
  }
  return row;
}

let SETTINGS_NOW = [];

function renderSettings(s) {
  SETTINGS_NOW = s.fields;
  const byKey = Object.fromEntries(s.fields.map((f) => [f.key, f]));
  $("#settings-fields").replaceChildren(
    ...SECTIONS.map(([title, keys]) => el("fieldset", { class: "panel settings" }, el("legend", {}, title),
      keys.filter((k) => byKey[k]).map((k) => settingRow(byKey[k], s.keys)))),
    el("p", { class: "hint" }, "Changes are kept in ", el("span", { class: "mono" }, s.saved_in), "."));
}

async function showSettings() {
  $("#settings-out").replaceChildren();
  $("#settings-fields").replaceChildren(busy("Reading the settings…"));
  try {
    renderSettings(await api("/api/settings"));
  } catch (e) {
    $("#settings-fields").replaceChildren(errorBox(e));
  }
}

async function saveSettings(changes) {
  const out = $("#settings-out");
  if (!Object.keys(changes).length) {
    out.replaceChildren(el("p", { class: "muted" }, "– nothing changed."));
    return;
  }
  out.replaceChildren(busy("Saving…"));
  try {
    renderSettings(await api("/api/settings", { changes }));
    out.replaceChildren(el("p", {}, st("ok", "✓ saved"), " and in use now."));
    loadStatus();
  } catch (e) {
    out.replaceChildren(errorBox(e));
  }
}

$("#settings-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const changes = {};
  for (const f of SETTINGS_NOW) {
    const input = form.elements[f.key];
    if (!input || input.disabled) continue;
    let v = f.kind === "bool" ? input.checked : input.value.trim();
    if (f.kind === "number") v = v === "" ? null : Number(v);
    const now = f.value ?? (f.kind === "bool" ? false : "");
    if (f.kind === "number" ? v !== null && v !== Number(now) : v !== now) changes[f.key] = v;
  }
  saveSettings(changes);
});

// --- Start: pick up a job that was running when the page was reloaded.

async function resume() {
  try {
    const jobs = await api("/api/jobs");
    const running = jobs.find((j) => j.state === "running");
    if (!running) return;
    if (running.kind === "batch") follow(running.id, $("#batch-out"));
    else if (running.kind === "paperless") follow(running.id, $("#paperless-out"));
    else if (running.kind === "maintenance") follow(running.id, $("#maint-job")).then(() => loadMaintenance());
    else follow(running.id, $("#sync-out"), attachButton);
  } catch { /* nothing to resume */ }
}

// Installing the page as an app needs a service worker, and browsers allow one only over HTTPS (or localhost).
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* the page works without it */ });
}

window.addEventListener("hashchange", route);
loadStatus();
route();
resume();
