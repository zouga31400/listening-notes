// Podcast Radio — Scriptable app (read-only view of the recorded episodes).
// Reads the podcast feed (RSS): episodes with picture, duration, keywords and the Claude
// summary. The feed address is private: it is kept in this device's keychain (asked once)
// and never appears in this file. Episodes are also kept on the device, so the statistics
// and the keyword search still cover episodes whose audio has been deleted from Drive.
// Tabs: Épisodes (search, one card per evening) and Statistiques (figures, keywords that
// open the episodes containing them).
// Reset the address: run the script with the parameter reset=1.

const VERSION = "2026-11-18 · 3";   // shown at the bottom of the app
const KEY = "podcast_radio_feed_url";
const fm = FileManager.local();
const DIR = fm.joinPath(fm.documentsDirectory(), "podcast-radio");
const COVERS = fm.joinPath(DIR, "covers");
const ARCHIVE = fm.joinPath(DIR, "episodes.json");
const COVER_SIDE = 360;   // pixels: covers are stored small, the list only shows thumbnails

// ---------- Feed address ----------
async function askUrl(again) {
  const a = new Alert();
  a.title = again ? "Adresse du flux refusée" : "Adresse du flux";
  a.message = "Colle l'adresse du flux du podcast (ligne « Podcast feed » du rapport reçu par email). " +
    "Elle reste dans le trousseau de cet appareil.";
  a.addSecureTextField("https://…", "");
  a.addAction("Enregistrer");
  a.addCancelAction("Annuler");
  if (await a.present() === -1) return null;
  const t = a.textFieldValue(0).trim();
  if (!/^https?:\/\//i.test(t)) return null;
  Keychain.set(KEY, t);
  return t;
}

async function getUrl() {
  if (Keychain.contains(KEY)) return Keychain.get(KEY);
  if (config.runsInWidget) return null;
  return askUrl(false);
}

// ---------- Feed parsing (the feed is written by the recording job: fixed format) ----------
function unesc(s) {
  return String(s || "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'").replace(/&#39;/g, "'").replace(/&amp;/g, "&");
}
const field = (xml, re) => { const m = xml.match(re); return m ? unesc(m[1]).trim() : ""; };

function seconds(s) {
  const p = String(s || "").split(":").map(Number);
  if (!p.length || p.some((n) => isNaN(n))) return 0;
  return p.reduce((t, n) => t * 60 + n, 0);
}

// "<prefix> 16/11/26 21:00-00:42" -> date and times of the recording
function parseTitle(title) {
  const m = title.match(/(\d{2})\/(\d{2})\/(\d{2}) (\d{2}):(\d{2})-(\d{2}):(\d{2})\s*$/);
  if (!m) return null;
  return {date: `20${m[3]}-${m[2]}-${m[1]}`, from: `${m[4]}:${m[5]}`, to: `${m[6]}:${m[7]}`};
}

// Description: sections "Mots-clés :", "Claude :", "Mistral :", "Résumé :", "Titres diffusés :"
const HEADER = /^(Mots-clés|Keywords|Claude|Mistral|Résumé|Summary|Titres diffusés|Tracks played) ?:$/;
const SECTION_KEY = {"Mots-clés": "keywords", "Keywords": "keywords", "Claude": "claude", "Mistral": "local",
  "Résumé": "summary", "Summary": "summary", "Titres diffusés": "tracks", "Tracks played": "tracks"};
function sections(text) {
  const out = {};
  let key = "summary", lines = [];
  const flush = () => { const t = lines.join("\n").trim(); if (t) out[key] = t; };
  for (const line of String(text || "").split("\n")) {
    const m = line.trim().match(HEADER);
    if (m) { flush(); key = SECTION_KEY[m[1]]; lines = []; } else lines.push(line);
  }
  flush();
  return out;
}

function keywordList(text) {
  const seen = new Set(), out = [];
  for (const k of String(text || "").split(",")) {
    const t = k.replace(/\s+/g, " ").trim().replace(/[.;]+$/, "").trim();
    if (t && t.length <= 60 && !seen.has(t.toLowerCase())) { seen.add(t.toLowerCase()); out.push(t); }
  }
  return out;
}

function parseFeed(xml) {
  const items = [];
  for (const m of xml.matchAll(/<item>([\s\S]*?)<\/item>/g)) {
    const x = m[1];
    const title = field(x, /<title>([\s\S]*?)<\/title>/);
    const when = parseTitle(title);
    const id = field(x, /<guid[^>]*>([\s\S]*?)<\/guid>/);
    if (!id || !when) continue;
    const s = sections(field(x, /<description>([\s\S]*?)<\/description>/));
    let duration = seconds(field(x, /<itunes:duration>([^<]*)<\/itunes:duration>/));
    if (!duration) {
      const [h1, m1] = when.from.split(":").map(Number), [h2, m2] = when.to.split(":").map(Number);
      duration = (((h2 * 60 + m2) - (h1 * 60 + m1) + 1440) % 1440) * 60;
    }
    items.push({id, title, ...when, duration, image: field(x, /<itunes:image href="([^"]*)"/),
      keywords: keywordList(s.keywords), claude: s.claude || "", local: s.local || "",
      summary: s.summary || "", tracks: s.tracks || ""});
  }
  return items;
}

// ---------- Storage (episodes and covers kept on the device) ----------
function loadArchive() {
  try { if (fm.fileExists(ARCHIVE)) return JSON.parse(fm.readString(ARCHIVE)); } catch (e) {}
  return {episodes: {}};
}
function ensureDirs() { if (!fm.fileExists(COVERS)) fm.createDirectory(COVERS, true); }
function saveArchive(a) {
  try { ensureDirs(); fm.writeString(ARCHIVE, JSON.stringify(a)); } catch (e) {}
}
const coverPath = (id) => fm.joinPath(COVERS, id.replace(/[^A-Za-z0-9_-]/g, "") + ".jpg");

// Downloads the cover once and keeps a small JPEG copy (Drive deletes it after 14 days).
async function keepCover(ep) {
  const path = coverPath(ep.id);
  if (!ep.image || fm.fileExists(path)) return;
  try {
    const r = new Request(ep.image);
    r.timeoutInterval = 20;
    const img = await r.loadImage();
    const w = img.size.width, h = img.size.height;
    const k = Math.min(1, COVER_SIDE / Math.max(w, h));
    const dc = new DrawContext();
    dc.size = new Size(Math.round(w * k), Math.round(h * k));
    dc.respectScreenScale = false;
    dc.opaque = true;
    dc.drawImageInRect(img, new Rect(0, 0, Math.round(w * k), Math.round(h * k)));
    fm.write(path, Data.fromJPEG(dc.getImage(), 0.75));
  } catch (e) {}
}

function coverUri(id) {
  try {
    const p = coverPath(id);
    return fm.fileExists(p) ? "data:image/jpeg;base64," + Data.fromFile(p).toBase64String() : "";
  } catch (e) { return ""; }
}

async function loadData() {
  let url = await getUrl();
  if (!url) return null;
  try { ensureDirs(); } catch (e) {}
  const archive = loadArchive();
  let xml = "", offline = "";
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const r = new Request(url);
      r.timeoutInterval = 30;
      xml = await r.loadString();
      const code = r.response.statusCode;
      if (code >= 400 || !/<rss/.test(xml)) { const e = new Error("feed"); e.code = code; throw e; }
      break;
    } catch (e) {
      xml = "";
      if (e.code && e.code < 500 && !config.runsInWidget && attempt === 0 && Object.keys(archive.episodes).length === 0) {
        Keychain.remove(KEY);
        url = await askUrl(true);
        if (!url) return null;
        continue;
      }
      offline = "Flux injoignable : épisodes enregistrés sur cet appareil";
      break;
    }
  }
  if (xml) {
    const inFeed = new Set();
    for (const ep of parseFeed(xml)) {
      inFeed.add(ep.id);
      archive.episodes[ep.id] = {...(archive.episodes[ep.id] || {}), ...ep};
      if (!config.runsInWidget) await keepCover(ep);
    }
    for (const id of Object.keys(archive.episodes)) archive.episodes[id].inFeed = inFeed.has(id);
    archive.at = new Date().toISOString();
    saveArchive(archive);
  }
  const episodes = Object.values(archive.episodes).map((e) => ({
    id: e.id, title: e.title, date: e.date, from: e.from, to: e.to, duration: e.duration,
    keywords: e.keywords || [], claude: e.claude || "", local: e.local || "",
    summary: e.summary || "", tracks: e.tracks || "", available: !!e.inFeed,
    cover: coverUri(e.id),
  }));
  if (!episodes.length) { const e = new Error("empty"); e.code = 0; throw e; }
  return {episodes, offline, at: archive.at || ""};
}

// ---------- Page (runs in the web view: written as a function, injected as text) ----------
function page(D) {
  const el = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; };
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const norm = (s) => String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[«»"“”‘’'`]/g, " ").replace(/\s+/g, " ").trim();
  const U = (s) => new Date(s + "T12:00:00Z");
  const fmt = (s, o) => new Intl.DateTimeFormat("fr-FR", Object.assign({timeZone: "UTC"}, o)).format(U(s));
  const dur = (sec) => { const m = Math.round(sec / 60), h = Math.floor(m / 60); return h ? h + " h " + String(m % 60).padStart(2, "0") : m + " min"; };
  const hm = (t) => t.replace(":", "h");
  const pad = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const today = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
  const dayDiff = (d) => Math.round((U(today) - U(d)) / 864e5);
  const groupLabel = (d) => {
    const n = dayDiff(d);
    if (n <= 0) return "Aujourd'hui";
    if (n === 1) return "Hier";
    if (n <= 6) return cap(fmt(d, {weekday: "long"}));
    if (n <= 13) return "La semaine dernière";
    const y = +d.slice(0, 4), m = +d.slice(5, 7), ty = +today.slice(0, 4), tm = +today.slice(5, 7);
    if (y * 12 + m === ty * 12 + tm - 1) return "Le mois dernier";
    if (y === ty && m === tm) return "Mois en cours";
    if (y === ty) return cap(fmt(d, {month: "long", year: "numeric"}));
    return String(y);
  };

  const E = D.episodes.map((e) => Object.assign({}, e, {
    kn: e.keywords.map(norm),
    main: e.claude || e.summary || e.local || "",
    text: norm([e.title, fmt(e.date, {weekday: "long", day: "numeric", month: "long"}), e.keywords.join(" "),
      e.claude, e.summary, e.local, e.tracks].join(" ")),
  })).sort((a, b) => (b.date + b.from).localeCompare(a.date + a.from));

  // Keywords: grouped without accents or case, shown in their most frequent spelling.
  const K = new Map();
  for (const e of E) {
    for (const k of new Set(e.kn)) {
      if (!K.has(k)) K.set(k, {key: k, forms: new Map(), eps: []});
      K.get(k).eps.push(e);
    }
    e.keywords.forEach((w) => { const o = K.get(norm(w)); o.forms.set(w, (o.forms.get(w) || 0) + 1); });
  }
  const KW = [...K.values()].map((o) => ({key: o.key, n: o.eps.length, eps: o.eps,
    label: [...o.forms].sort((a, b) => b[1] - a[1])[0][0]}))
    .sort((a, b) => b.n - a.n || a.label.localeCompare(b.label, "fr"));
  const kwByKey = new Map(KW.map((k) => [k.key, k]));

  const POS = /(\d+:\d{2}:\d{2}(?: \(\d{2}h\d{2}\))?)/;
  const rich = (parent, text) => text.split(POS).forEach((part, i) => {
    if (!part) return;
    if (i % 2) parent.append(el("span", "pos", part)); else parent.append(document.createTextNode(part));
  });
  function summary(box, text) {
    let ul = null;
    for (const line of text.split("\n")) {
      const t = line.trim();
      if (!t) { ul = null; continue; }
      if (t.startsWith("- ")) {
        if (!ul) { ul = el("ul"); box.append(ul); }
        const li = el("li"); rich(li, t.slice(2)); ul.append(li);
      } else {
        ul = null;
        const m = t.match(/^([^:]{2,40}) ?:\s*(.*)$/);
        const p = el("p");
        if (m && !/\d/.test(m[1])) { p.append(el("b", null, m[1] + " :")); if (m[2]) { p.append(" "); rich(p, m[2]); } }
        else rich(p, t);
        box.append(p);
      }
    }
  }
  function lead(e) {
    const s = e.main;
    const m = s.match(/^En bref\s*:\s*([\s\S]*?)(?:\n\s*\n|\nAu fil)/);
    return (m ? m[1] : s.split("\n\n")[0]).replace(/\s+/g, " ").trim();
  }

  let tab = "eps", q = "", st = {q: "", kw: "", all: false};

  function cover(e, c) {
    if (e.cover) { const i = el("img", c); i.alt = ""; i.src = e.cover; return i; }
    return el("div", c + " none");
  }
  function chips(e, max) {
    const box = el("div", "chips");
    e.keywords.slice(0, max).forEach((w) => { const c = el("span", "chip", w); box.append(c); });
    if (e.keywords.length > max) box.append(el("span", "chip more", "+" + (e.keywords.length - max)));
    return box;
  }
  function meta(e) {
    return hm(e.from) + "–" + hm(e.to) + " · " + dur(e.duration) + (e.available ? "" : " · audio supprimé");
  }
  function card(e) {
    const a = el("div", "card" + (e.available ? "" : " old"));
    a.append(cover(e, "cover"));
    const m = el("div", "meta");
    m.append(el("div", "t", cap(fmt(e.date, {weekday: "long", day: "numeric", month: "long"}))), el("div", "d", meta(e)));
    if (e.keywords.length) m.append(chips(e, 4));
    const l = lead(e);
    if (l) m.append(el("div", "lead", l));
    a.append(m);
    a.onclick = () => openDetail(e);
    return a;
  }
  function row(e) {
    const a = el("div", "row");
    a.append(cover(e, "thumb"));
    const m = el("div", "meta");
    m.append(el("div", "t", cap(fmt(e.date, {weekday: "short", day: "numeric", month: "long", year: "numeric"}))),
      el("div", "d", dur(e.duration)));
    const l = lead(e);
    if (l) m.append(el("div", "lead small", l));
    a.append(m);
    a.onclick = () => openDetail(e);
    return a;
  }

  // ----- Detail -----
  function openDetail(e) {
    const d = $("detail");
    d.replaceChildren();
    const back = el("button", "back", "‹ Retour");
    back.onclick = closeDetail;
    d.append(back);
    const wrap = el("div", "dwrap");
    wrap.append(cover(e, "dcover"));
    wrap.append(el("h2", "dt", cap(fmt(e.date, {weekday: "long", day: "numeric", month: "long", year: "numeric"}))));
    wrap.append(el("div", "d", meta(e)));
    if (e.available) {
      // The apps are only opened (no link to one episode): the latest ones are at the top.
      const play = el("div", "play");
      const a1 = el("a", "btn", "▶ Overcast");
      a1.href = "overcast://";
      const a2 = el("a", "btn", "▶ Apple Podcasts");
      a2.href = "podcasts://";
      play.append(a1, a2);
      wrap.append(play);
    }
    if (!e.available) wrap.append(el("div", "warn", "L'audio n'est plus sur Drive : le résumé et les mots-clés sont conservés ici."));
    if (e.keywords.length) {
      wrap.append(el("h3", null, "Mots-clés"));
      const box = el("div", "chips wrap");
      e.keywords.forEach((w) => {
        const c = el("button", "chip tap", w);
        c.onclick = () => { closeDetail(); st.kw = norm(w); st.q = ""; setTab("stats"); };
        box.append(c);
      });
      wrap.append(box);
    }
    wrap.append(el("h3", null, e.claude ? "Résumé" : (e.summary ? "Résumé" : "Résumé (Mistral)")));
    if (e.main) { const s = el("div", "sum"); summary(s, e.main); wrap.append(s); }
    else wrap.append(el("div", "d", "Pas encore de résumé pour cet épisode."));
    const fold = (title, text) => {
      const dt = el("details"); dt.append(el("summary", null, title));
      const s = el("div", "sum"); summary(s, text); dt.append(s); wrap.append(dt);
    };
    if (e.claude && e.local) fold("Résumé de Mistral", e.local);
    if (e.tracks) fold("Titres diffusés", e.tracks);
    d.append(wrap);
    d.hidden = false;
    document.body.classList.add("lock");
    d.scrollTop = 0;
  }
  function closeDetail() { $("detail").hidden = true; document.body.classList.remove("lock"); }

  // ----- Tabs -----
  function groups(list) {
    const g = new Map();
    for (const e of list) { const k = groupLabel(e.date); if (!g.has(k)) g.set(k, []); g.get(k).push(e); }
    return g;
  }
  function renderEpisodes() {
    const M = $("main"); M.replaceChildren();
    const i = el("input"); i.type = "search"; i.placeholder = "Rechercher un épisode…"; i.value = q;
    M.append(i);
    const out = el("div"); M.append(out);
    const fill = () => {
      out.replaceChildren();
      const n = norm(q);
      const list = E.filter((e) => !n || e.text.includes(n));
      if (!list.length) { out.append(el("div", "empty", n ? "Aucun résultat." : "Aucun épisode.")); return; }
      for (const [label, items] of groups(list)) { out.append(el("h2", null, label)); items.forEach((e) => out.append(card(e))); }
    };
    i.oninput = () => { q = i.value; fill(); };
    fill();
  }

  function renderStats() {
    const M = $("main"); M.replaceChildren();
    const total = E.reduce((t, e) => t + e.duration, 0);
    const k = el("div", "kpis");
    const kpi = (n, l) => { const d = el("div", "kpi"); d.append(el("b", null, String(n)), el("span", null, l)); k.append(d); };
    kpi(E.length, "épisodes"); kpi(dur(total), "d'écoute au total");
    kpi(dur(total / Math.max(1, E.length)), "par épisode"); kpi(KW.length, "mots-clés distincts");
    M.append(k);

    const sb = el("div", "box");
    sb.append(el("h3", null, "Rechercher"));
    const i = el("input"); i.type = "search"; i.placeholder = "Un mot-clé, un mot…"; i.value = st.q;
    sb.append(i);
    const cloud = el("div", "cloud"), res = el("div", "res");
    sb.append(cloud);
    M.append(sb, res);

    const draw = () => {
      const n = norm(st.q);
      cloud.replaceChildren(); res.replaceChildren();
      let list = n ? KW.filter((x) => x.key.includes(n)) : KW;
      const more = !n && !st.all && list.length > 60;
      if (more) list = list.slice(0, 60);
      const mx = Math.max(1, ...KW.map((x) => x.n));
      if (!list.length) cloud.append(el("div", "d", n ? "Aucun mot-clé ne correspond." : "Aucun mot-clé pour l'instant."));
      list.forEach((x) => {
        const b = el("button", "kw" + (st.kw === x.key ? " on" : ""), x.label);
        b.style.fontSize = (13 + Math.round(6 * Math.sqrt((x.n - 1) / Math.max(1, mx - 1)))) + "px";
        if (x.n > 1) b.append(el("sup", null, String(x.n)));
        b.onclick = () => { st.kw = st.kw === x.key ? "" : x.key; draw(); if (st.kw) res.scrollIntoView({behavior: "smooth", block: "start"}); };
        cloud.append(b);
      });
      if (more) { const b = el("button", "link", "Voir les " + KW.length + " mots-clés"); b.onclick = () => { st.all = true; draw(); }; cloud.append(b); }

      let eps = [], title = "";
      if (st.kw && kwByKey.has(st.kw)) {
        const o = kwByKey.get(st.kw); eps = o.eps; title = "« " + o.label + " » · " + eps.length + " épisode" + (eps.length > 1 ? "s" : "");
      } else if (n) {
        eps = E.filter((e) => e.text.includes(n)); title = "Épisodes contenant « " + st.q.trim() + " » · " + eps.length;
      }
      if (title) {
        const h = el("div", "reshead"); h.append(el("h2", null, title));
        if (st.kw) { const x = el("button", "link", "Effacer"); x.onclick = () => { st.kw = ""; draw(); }; h.append(x); }
        res.append(h);
        if (!eps.length) res.append(el("div", "empty", "Aucun épisode."));
        const box = el("div", "rows"); eps.forEach((e) => box.append(row(e))); res.append(box);
      }
    };
    i.oninput = () => { st.q = i.value; st.kw = ""; draw(); };

    // Most frequent keywords
    const top = KW.filter((x) => x.n > 1).slice(0, 8);
    if (top.length) {
      const c = el("div", "box"); c.append(el("h3", null, "Mots-clés les plus fréquents"));
      top.forEach((x) => {
        const b = el("div", "hb tap"); const tr = el("div", "tr"); const f = el("i"); f.style.width = (100 * x.n / top[0].n) + "%"; tr.append(f);
        b.append(el("span", "name", x.label), tr, el("span", "n", String(x.n)));
        b.onclick = () => { st.kw = x.key; st.q = ""; i.value = ""; draw(); res.scrollIntoView({behavior: "smooth", block: "start"}); };
        c.append(b);
      });
      M.append(c);
    }
    // Duration of the latest evenings
    const last = E.slice(0, 14).reverse();
    const mx = Math.max(1, ...last.map((e) => e.duration));
    const c = el("div", "box"); c.append(el("h3", null, "Durée des dernières soirées"));
    const cols = el("div", "cols");
    last.forEach((e) => {
      const d = el("div"); const b = el("i"); b.style.height = (85 * e.duration / mx) + "%";
      d.append(el("em", null, (e.duration / 3600).toFixed(1).replace(".", ",")), b, el("span", null, fmt(e.date, {day: "2-digit", month: "2-digit"})));
      d.onclick = () => openDetail(e); cols.append(d);
    });
    c.append(cols); M.append(c);
    // Episodes per month
    const pm = new Map();
    E.forEach((e) => pm.set(e.date.slice(0, 7), (pm.get(e.date.slice(0, 7)) || 0) + 1));
    if (pm.size > 1) {
      const mo = [...pm].sort().slice(-12), top2 = Math.max(...mo.map((x) => x[1]));
      const b2 = el("div", "box"); b2.append(el("h3", null, "Épisodes par mois"));
      mo.forEach(([m, n]) => {
        const h = el("div", "hb"); const tr = el("div", "tr"); const f = el("i"); f.style.width = (100 * n / top2) + "%"; tr.append(f);
        h.append(el("span", "name", cap(fmt(m + "-15", {month: "long", year: "numeric"}))), tr, el("span", "n", String(n))); b2.append(h);
      });
      M.append(b2);
    }
    draw();
  }

  function setTab(t) {
    tab = t;
    document.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
    if (t === "eps") renderEpisodes(); else renderStats();
    window.scrollTo(0, 0);
  }
  function $(id) { return document.getElementById(id); }

  $("ver").textContent = "Version " + (D.version || "?");
  $("sub").textContent = E.length + " épisode" + (E.length > 1 ? "s" : "") + " · " + KW.length + " mots-clés";
  if (D.offline) $("warn").append(el("div", "warn", D.offline));
  document.querySelectorAll(".seg button").forEach((b) => { b.onclick = () => setTab(b.dataset.tab); });
  setTab("eps");
}

const CSS = `
:root{--bg:#f4f5f7;--card:#fff;--fg:#14161a;--muted:#6b717c;--line:#e3e5e9;--accent:#2f6fde;--seg:#e6e8ec;--segon:#fff;--warn-bg:#fbefcc;--warn:#7a5800}
@media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1f;--fg:#f5f5f7;--muted:#9a9aa2;--line:#2b2b30;--accent:#7fb3ff;--seg:#1f1f23;--segon:#3a3a40;--warn-bg:#4a3a12;--warn:#ffd166}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--fg);font:16px -apple-system,system-ui,sans-serif}
body{padding:calc(env(safe-area-inset-top) + 8px) max(16px,env(safe-area-inset-right)) calc(env(safe-area-inset-bottom) + 28px) max(16px,env(safe-area-inset-left))}
body.lock{overflow:hidden}
h1{font-size:34px;margin:6px 0 2px;letter-spacing:-.5px}.sub{color:var(--muted);margin:0 0 14px;font-size:15px}
.warn{border-radius:12px;padding:10px 14px;font-size:14px;margin:10px 0;background:var(--warn-bg);color:var(--warn)}
.seg{display:flex;background:var(--seg);border-radius:10px;padding:2px;margin-bottom:14px;position:sticky;top:env(safe-area-inset-top);z-index:2}
.seg button{flex:1;border:0;background:none;color:var(--fg);font:inherit;font-size:13px;padding:8px 0;border-radius:8px}
.seg button.on{background:var(--segon);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.2)}
input{width:100%;font:inherit;padding:10px 12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--fg);margin-bottom:6px}
h2{font-size:21px;margin:22px 0 10px;letter-spacing:-.2px}h3{margin:18px 0 8px;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.card{display:flex;gap:14px;align-items:flex-start;background:var(--card);border-radius:20px;padding:12px;margin-bottom:12px}
.cover{width:104px;height:104px;border-radius:12px;flex:none;object-fit:cover;background:linear-gradient(135deg,#4a4f5a,#23262c);box-shadow:0 2px 8px rgba(0,0,0,.25)}
.card.old{opacity:.82}
.meta{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}
.t{font-size:18px;font-weight:700;line-height:1.2}.d{font-size:14px;color:var(--muted)}
.lead{font-size:14px;line-height:1.35;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;margin-top:2px}.lead.small{-webkit-line-clamp:2}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin:3px 0;max-height:52px;overflow:hidden}.chips.wrap{max-height:none}
.chip{font-size:12px;padding:2px 9px;border-radius:99px;background:var(--seg);color:var(--muted);font-weight:600;border:0;font-family:inherit;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip.more{background:none;padding-left:2px}.chip.tap{color:var(--accent);font-size:13px;padding:5px 12px;white-space:normal;text-align:left}
.box{background:var(--card);border-radius:16px;padding:14px;margin-bottom:12px}.box h3{margin:0 0 10px}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}.kpi{background:var(--card);border-radius:16px;padding:14px}
.kpi b{display:block;font-size:28px;font-variant-numeric:tabular-nums}.kpi span{font-size:13px;color:var(--muted)}
.cloud{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.kw{border:0;font-family:inherit;border-radius:99px;background:var(--seg);color:var(--fg);padding:5px 12px;font-weight:600}
.kw.on{background:var(--accent);color:#fff}.kw sup{font-size:10px;margin-left:3px;opacity:.7}
.link{border:0;background:none;color:var(--accent);font:inherit;font-size:14px;font-weight:600;padding:5px 4px}
.reshead{display:flex;align-items:center;justify-content:space-between;gap:10px}.reshead h2{margin:18px 0 10px;font-size:19px}
.rows{display:flex;flex-direction:column;gap:8px;margin-bottom:12px}
.row{display:flex;gap:12px;background:var(--card);border-radius:14px;padding:10px}.row .t{font-size:16px}.thumb{width:56px;height:56px;border-radius:9px;object-fit:cover;flex:none;background:linear-gradient(135deg,#4a4f5a,#23262c)}
.cols{display:flex;align-items:flex-end;gap:6px;height:130px;overflow-x:auto}.cols div{flex:1 0 28px;display:flex;flex-direction:column;align-items:center;gap:3px;font-size:10px;color:var(--muted);height:100%;justify-content:flex-end}
.cols i{display:block;width:100%;background:var(--accent);border-radius:4px 4px 0 0;min-height:2px}.cols em{font-style:normal;color:var(--fg);font-size:11px}
.hb{display:grid;grid-template-columns:130px 1fr 26px;gap:8px;align-items:center;font-size:14px;margin:8px 0}.hb .n{text-align:right;font-variant-numeric:tabular-nums}
.hb .tr{height:10px;border-radius:5px;background:var(--line);overflow:hidden}.hb .tr i{display:block;height:100%;border-radius:5px;background:var(--accent)}
.hb .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#detail{position:fixed;inset:0;background:var(--bg);overflow-y:auto;z-index:5;padding:calc(env(safe-area-inset-top) + 8px) max(16px,env(safe-area-inset-right)) calc(env(safe-area-inset-bottom) + 28px) max(16px,env(safe-area-inset-left))}
#detail[hidden]{display:none}.back{border:0;background:none;color:var(--accent);font:inherit;font-size:17px;padding:8px 0;position:sticky;top:0}
.dwrap{max-width:640px;margin:0 auto}.dcover{display:block;width:min(100%,260px);aspect-ratio:1;object-fit:cover;border-radius:18px;margin:6px auto 14px;box-shadow:0 4px 16px rgba(0,0,0,.3);background:linear-gradient(135deg,#4a4f5a,#23262c)}
.dcover.none{height:0;aspect-ratio:auto;margin:0;box-shadow:none}.dt{margin:6px 0 2px;font-size:26px}
.sum p{margin:10px 0;line-height:1.45}.sum ul{padding-left:18px;margin:8px 0}.sum li{margin:7px 0;line-height:1.4}
.pos{font-variant-numeric:tabular-nums;font-size:13px;font-weight:600;color:var(--accent);white-space:nowrap}
details{background:var(--card);border-radius:14px;padding:10px 14px;margin-top:12px}summary{font-weight:600}
.ver{color:var(--muted);font-size:12px;text-align:center;margin:28px 0 0}.empty{color:var(--muted);padding:28px 4px;text-align:center}
.tap{cursor:pointer}
.play{display:flex;gap:10px;margin:14px 0 4px}.btn{flex:1;text-align:center;text-decoration:none;font-weight:600;font-size:15px;padding:11px 8px;border-radius:12px;background:var(--accent);color:#fff}
`;

function html(data) {
  const json = JSON.stringify(data).replace(/</g, "\\u003c");
  return `<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>${CSS}</style></head><body>
<h1>Podcast</h1><p class="sub" id="sub"></p><div id="warn"></div>
<div class="seg"><button data-tab="eps" class="on">Épisodes</button><button data-tab="stats">Statistiques</button></div>
<main id="main"></main>
<p class="ver" id="ver"></p>
<div id="detail" hidden></div>
<script>(${page.toString()})(${json});</script></body></html>`;
}

// ---------- App ----------
async function app(data) {
  const wv = new WebView();
  // Podcast app links (overcast:, podcasts:) open outside the web view.
  wv.shouldAllowRequest = (req) => {
    const u = req.url || "";
    if (/^(overcast|podcast|podcasts):/i.test(u)) { Safari.open(u); return false; }
    return true;
  };
  data.version = VERSION;
  await wv.loadHTML(html(data));
  await wv.present(true);
}

// ---------- Main ----------
if (args.queryParameters && args.queryParameters.reset && Keychain.contains(KEY)) Keychain.remove(KEY);
let data = null, error = null;
try { data = await loadData(); } catch (e) { error = e; }
if (config.runsInWidget) {
  const w = new ListWidget();
  w.backgroundColor = new Color("#16181d");
  const t = w.addText("Ouvre l'app Podcast pour voir les épisodes.");
  t.textColor = Color.white(); t.font = Font.systemFont(12);
  Script.setWidget(w);
} else if (error) {
  const a = new Alert();
  a.title = "Podcast";
  a.message = error.message === "empty"
    ? "Aucun épisode dans le flux pour l'instant."
    : `Chargement impossible : ${error.message}`;
  a.addAction("OK");
  a.addAction("Saisir à nouveau l'adresse du flux");
  if (await a.present() === 1) {
    Keychain.remove(KEY);
    const url = await askUrl(false);
    if (url) { try { data = await loadData(); if (data) await app(data); } catch (e) {} }
  }
} else if (data) {
  await app(data);
}
Script.complete();
