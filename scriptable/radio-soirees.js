// Radio Soirées — Scriptable app and widget (read-only view of the Notion history).
// Reads the two Notion databases with a READ-ONLY Notion connection. Its key is kept in
// this device's keychain (asked once); it never appears in this file.
// Widget: add a Scriptable widget to the home screen and choose this script.

const VERSION = "2026-11-20 · 19";   // shown at the bottom of the app
const KEY = "radio_soirees_notion_key";
const HISTORY_DB = "1099479c43a944e0a358727716a0d49b";
const MISSING_DB = "0cfa728c14294956be448e18eb460be1";
const ARTISTS_DB = "41d481d1393d81b095ece915e9b186b9";   // biography, albums, influences, similar artists
const SHOWS_DB = "41d481d1393d8104ae44d71455c86767";   // show start and end of each evening
const API = "https://api.notion.com/v1";
const CACHE = FileManager.local().joinPath(FileManager.local().cacheDirectory(), "radio-soirees.json");

// ---------- Notion ----------
async function askKey(again) {
  const a = new Alert();
  a.title = again ? "Clé Notion refusée" : "Clé Notion";
  a.message = "Colle la clé de la connexion Notion en lecture seule (elle commence par ntn_ ou secret_). " +
    "Elle reste dans le trousseau de cet appareil.";
  a.addSecureTextField("ntn_…", "");
  a.addAction("Enregistrer");
  a.addCancelAction("Annuler");
  if (await a.present() === -1) return null;
  const t = a.textFieldValue(0).trim();
  if (!t) return null;
  Keychain.set(KEY, t);
  return t;
}

async function getKey() {
  if (Keychain.contains(KEY)) return Keychain.get(KEY);
  if (config.runsInWidget) return null;
  return askKey(false);
}

function prop(p) {
  if (!p) return null;
  switch (p.type) {
    case "title": return p.title.map((t) => t.plain_text).join("");
    case "rich_text": {   // long texts are stored in parts cut on spaces: put the space back between parts
      let out = "";
      for (const t of p.rich_text) {
        const x = t.plain_text;
        out += out && !/\s$/.test(out) && !/^\s/.test(x) ? " " + x : x;
      }
      return out;
    }
    case "select": return p.select ? p.select.name : "";
    case "number": return p.number;
    case "url": return p.url || "";
    case "date": return p.date ? p.date.start : null;
    case "files": {
      const f = p.files && p.files[0];
      return f ? (f.external ? f.external.url : (f.file ? f.file.url : "")) : "";
    }
    default: return null;
  }
}

async function queryAll(key, db, sortProp, maxPages) {
  const rows = [];
  let cursor;
  for (let i = 0; i < maxPages; i++) {
    const r = new Request(`${API}/databases/${db}/query`);
    r.method = "POST";
    r.headers = {"Authorization": `Bearer ${key}`, "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"};
    const body = {page_size: 100, sorts: [{property: sortProp, direction: "descending"}]};
    if (cursor) body.start_cursor = cursor;
    r.body = JSON.stringify(body);
    const j = await r.loadJSON();
    const code = r.response.statusCode;
    if (code === 401) { const e = new Error("unauthorized"); e.code = 401; throw e; }
    if (code === 404) { const e = new Error("not_shared"); e.code = 404; throw e; }
    if (code >= 400) throw new Error(`Notion ${code}: ${(j && j.message) || ""}`);
    rows.push(...j.results);
    if (!j.has_more) break;
    cursor = j.next_cursor;
  }
  return rows;
}

// Last run of the listening workflow (public repository: no key needed).
async function ciStatus() {
  try {
    const r = new Request("https://api.github.com/repos/zouga31400/listening-notes/actions/workflows/radio.yml/runs?per_page=10");
    r.headers = {"Accept": "application/vnd.github+json"};
    r.timeoutInterval = 10;
    const j = await r.loadJSON();
    if (r.response.statusCode !== 200) return null;
    const run = (j.workflow_runs || []).find((x) => x.status === "completed" &&
      (x.event === "workflow_dispatch" || x.event === "schedule"));
    if (!run) return null;
    return {id: run.id, ok: run.conclusion === "success" || run.conclusion === "cancelled" || run.conclusion === "skipped",
            conclusion: run.conclusion, at: run.updated_at, url: run.html_url};
  } catch (e) { return null; }
}

function notifyFailure(ci) {
  // Best effort: iOS decides when widgets refresh, so this is not a reliable alarm.
  try {
    const fm = FileManager.local();
    const f = fm.joinPath(fm.cacheDirectory(), "radio-soirees-ci.txt");
    const seen = fm.fileExists(f) ? fm.readString(f) : "";
    if (ci && !ci.ok && String(ci.id) !== seen) {
      const n = new Notification();
      n.title = "Radio Soirées";
      n.body = "La dernière exécution du workflow a échoué.";
      n.openURL = ci.url;
      n.schedule();
      fm.writeString(f, String(ci.id));
    } else if (ci && ci.ok && seen) { fm.writeString(f, ""); }
  } catch (e) {}
}

async function loadData() {
  let key = await getKey();
  if (!key) return null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const [h, m, sh, ar] = await Promise.all([
        queryAll(key, HISTORY_DB, "Played at", config.runsInWidget ? 1 : 20),
        queryAll(key, MISSING_DB, "Date", 5),
        // Not shared with the connection yet, or not available: the app falls back on estimates.
        config.runsInWidget ? [] : queryAll(key, SHOWS_DB, "Date", 5).catch(() => []),
        // Artist pages: not shared with the connection yet, or not available: no artist page.
        config.runsInWidget ? [] : queryAll(key, ARTISTS_DB, "Name", 10).catch(() => []),
      ]);
      const artists = ar.map((pg) => {
        const p = pg.properties;
        return {name: prop(p.Name) || "", country: prop(p.Country) || "", bio: prop(p.Bio) || "",
                source: prop(p["Bio source"]) || "", albums: prop(p.Albums) || "",
                influences: prop(p.Influences) || "", similar: prop(p.Similar) || "", image: prop(p.Image) || ""};
      }).filter((x) => x.name);
      const shows = sh.map((pg) => {
        const p = pg.properties;
        return {date: prop(p.Date) || "", start: prop(p.Start) || "", startDetected: prop(p["Start detected"]) || "",
                end: prop(p.End) || "", endDetected: prop(p["End detected"]) || "", source: prop(p["End source"]) || ""};
      }).filter((x) => x.date && x.start);
      const history = h.map((pg) => {
        const p = pg.properties;
        return {
          id: pg.id, start: prop(p["Start time"]) || prop(p["Played at"]),
          title: prop(p.Title) || "", artist: prop(p.Artist) || "", album: prop(p.Album) || "",
          year: prop(p.Year), duration: prop(p["Duration (s)"]) || 0, decade: prop(p.Decade) || "", genre: prop(p.Genre) || "",
          mood: prop(p.Mood) || "", status: prop(p.Status) || "", link: prop(p.Link) || "",
          label: prop(p.Label) || "", country: prop(p.Country) || "",
          albumLink: prop(p["Album link"]) || "", position: prop(p.Track) || "",
          dayPlaylist: prop(p["Day playlist"]) || "", monthPlaylist: prop(p["Month playlist"]) || "",
          shazam: prop(p.Shazam) || "", youtube: prop(p.YouTube) || "",
          cover: prop(p["Cover URL"]) || prop(p.Cover) || "",
        };
      }).filter((r) => r.start && r.status !== "Remove");
      const missing = m.map((pg) => {
        const p = pg.properties;
        return {id: pg.id, date: prop(p.Date) || "", title: prop(p.Title) || "", artist: prop(p.Artist) || "",
                album: prop(p.Album) || "", year: prop(p.Year), shazam: prop(p.Shazam) || "",
                youtube: prop(p.YouTube) || "", cover: prop(p.Cover) || "", genre: prop(p.Genre) || ""};
      });
      const ci = await ciStatus();
      notifyFailure(ci);
      const data = {history, missing, shows, artists, ci, at: new Date().toISOString()};
      if (!config.runsInWidget) FileManager.local().writeString(CACHE, JSON.stringify(data));
      return data;
    } catch (e) {
      if (e.code === 401 && !config.runsInWidget && attempt === 0) {
        Keychain.remove(KEY);
        key = await askKey(true);
        if (!key) return null;
        continue;
      }
      if (FileManager.local().fileExists(CACHE)) {
        const d = JSON.parse(FileManager.local().readString(CACHE));
        d.offline = e.code === 404 ? "Base non partagée avec la connexion" : "Hors ligne : dernières données enregistrées";
        return d;
      }
      throw e;
    }
  }
  return null;
}

// ---------- Dates (Paris) ----------
function parisParts(d) {
  try {
    const o = {};
    for (const p of new Intl.DateTimeFormat("fr-FR", {timeZone: "Europe/Paris", year: "numeric", month: "2-digit",
      day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).formatToParts(d)) o[p.type] = p.value;
    return o;
  } catch (e) {
    const z = (n) => String(n).padStart(2, "0");
    return {year: d.getFullYear(), month: z(d.getMonth() + 1), day: z(d.getDate()), hour: z(d.getHours()), minute: z(d.getMinutes())};
  }
}
const eveningOf = (d) => { const p = parisParts(new Date(d.getTime() - 6 * 3600e3)); return `${p.year}-${p.month}-${p.day}`; };
const timeOf = (d) => { const p = parisParts(d); return `${p.hour}h${p.minute}`; };
function eveningLabel(ev) {
  if (ev === eveningOf(new Date())) return "Ce soir";
  if (ev === eveningOf(new Date(Date.now() - 864e5))) return "Hier";
  try { return new Intl.DateTimeFormat("fr-FR", {timeZone: "UTC", weekday: "short", day: "numeric", month: "short"}).format(new Date(ev + "T12:00:00Z")); }
  catch (e) { return ev; }
}

// ---------- Widget ----------
async function image(url) {
  if (!url) return null;
  try { const r = new Request(url); r.timeoutInterval = 10; return await r.loadImage(); } catch (e) { return null; }
}

async function widget(data) {
  const w = new ListWidget();
  w.url = URLScheme.forRunningScript();   // a tap opens Scriptable and runs this script
  w.backgroundColor = new Color("#16181d");
  w.refreshAfterDate = new Date(Date.now() + 15 * 60e3);
  const fam = config.widgetFamily || "medium";
  const white = Color.white(), grey = new Color("#a7acb5");
  if (!data) { const t = w.addText("Ouvre l'app une fois pour saisir la clé Notion."); t.textColor = white; t.font = Font.systemFont(12); return w; }
  const tracks = data.history.filter((r) => r.status !== "Excerpt");
  if (!tracks.length) { const t = w.addText("Aucun titre pour l'instant."); t.textColor = white; return w; }
  const last = tracks[0];
  const ev = eveningOf(new Date(last.start));
  const count = tracks.filter((r) => eveningOf(new Date(r.start)) === ev).length;
  const label = `${eveningLabel(ev).toUpperCase()} · ${count} TITRE${count > 1 ? "S" : ""}`;

  if (fam === "small") {
    const img = await image(last.cover);
    if (img) { w.backgroundImage = img; w.backgroundGradient = null; }
    const g = new LinearGradient(); g.colors = [new Color("#000000", 0.05), new Color("#000000", 0.75)]; g.locations = [0.3, 1];
    w.backgroundGradient = g;
    w.addSpacer();
    if (data.ci && !data.ci.ok) { const f = w.addText("⚠️ Exécution en échec"); f.font = Font.boldSystemFont(9); f.textColor = new Color("#ff8a80"); }
    const l = w.addText(label); l.font = Font.boldSystemFont(9); l.textColor = new Color("#e4e6ea");
    const t = w.addText(last.title); t.font = Font.boldSystemFont(13); t.textColor = white; t.lineLimit = 2;
    const a = w.addText(`${last.artist} · ${timeOf(new Date(last.start))}`); a.font = Font.systemFont(10); a.textColor = new Color("#e4e6ea"); a.lineLimit = 1;
    return w;
  }

  if (data.ci && !data.ci.ok) {
    const f = w.addText("⚠️ Dernière exécution en échec"); f.font = Font.boldSystemFont(10); f.textColor = new Color("#ff8a80");
    w.addSpacer(2);
  }
  const head = w.addText(label); head.font = Font.boldSystemFont(10); head.textColor = grey;
  w.addSpacer(6);
  const n = fam === "large" ? 6 : 1;
  for (const r of tracks.slice(0, n)) {
    const row = w.addStack(); row.centerAlignContent();
    const img = await image(r.cover);
    const size = fam === "large" ? 44 : 100;
    if (img) { const i = row.addImage(img); i.imageSize = new Size(size, size); i.cornerRadius = 8; }
    else { const s = row.addStack(); s.size = new Size(size, size); s.backgroundColor = new Color("#2a2d33"); s.cornerRadius = 8; }
    row.addSpacer(10);
    const col = row.addStack(); col.layoutVertically();
    const t = col.addText(r.title); t.font = Font.boldSystemFont(fam === "large" ? 13 : 15); t.textColor = white; t.lineLimit = 2;
    const a = col.addText(r.artist); a.font = Font.systemFont(12); a.textColor = new Color("#c9ccd2"); a.lineLimit = 1;
    const m = col.addText([timeOf(new Date(r.start)), r.mood, r.year].filter(Boolean).join(" · ")); m.font = Font.systemFont(11); m.textColor = grey;
    row.addSpacer();
    if (fam === "large") w.addSpacer(6);
  }
  if (fam !== "large") w.addSpacer();
  return w;
}

// ---------- App (web view) ----------
function html(data) {
  const json = JSON.stringify(data).replace(/</g, "\\u003c");
  return `<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>
:root{--bg:#f4f5f7;--card:#fff;--fg:#14161a;--muted:#6b717c;--line:#e3e5e9;--accent:#2f6fde;--seg:#e6e8ec;--segon:#fff;
--calme-bg:#dcecfb;--calme:#1d5f99;--groove-bg:#fbefcc;--groove:#7a5800;--ener-bg:#fbdcdc;--ener:#9b2222;--exc-bg:#ffe8cc;--exc:#8a4b00;--exc-ribbon:#ff9f43;--abs-bg:#efe1ff;--abs:#6a2fb0;--abs-ribbon:#9b4dff}
@media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1f;--fg:#f5f5f7;--muted:#9a9aa2;--line:#2b2b30;--accent:#7fb3ff;--seg:#1f1f23;--segon:#3a3a40;
--calme-bg:#173a52;--calme:#7cc4ff;--groove-bg:#4a3a12;--groove:#ffd166;--ener-bg:#4d1d1d;--ener:#ff8a80;--exc-bg:#4d3112;--exc:#ffb35c;--exc-ribbon:#ff9f43;--abs-bg:#33214a;--abs:#cfa6ff;--abs-ribbon:#b565ff}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--fg);font:16px -apple-system,system-ui,sans-serif}
body{padding:calc(env(safe-area-inset-top) + 8px) max(16px,env(safe-area-inset-right)) calc(env(safe-area-inset-bottom) + 28px) max(16px,env(safe-area-inset-left))}
h1{font-size:34px;margin:6px 0 2px;letter-spacing:-.5px}.sub{color:var(--muted);margin:0 0 14px;font-size:15px}
.warn{border-radius:12px;padding:10px 14px;font-size:14px;margin-bottom:10px;background:var(--groove-bg);color:var(--groove)}
.warn.bad{background:var(--ener-bg);color:var(--ener);font-weight:600}.warn a{color:inherit}
.seg{display:flex;background:var(--seg);border-radius:10px;padding:2px;margin-bottom:14px;position:sticky;top:env(safe-area-inset-top);z-index:2}
.seg button{flex:1;border:0;background:none;color:var(--fg);font:inherit;font-size:13px;padding:8px 0;border-radius:8px}
.seg button.on{background:var(--segon);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.2)}
.bar{display:flex;gap:8px;margin-bottom:10px}
select,input{font:inherit;padding:10px 12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.bar input{flex:1;min-width:0}.view{display:flex;background:var(--seg);border-radius:10px;padding:2px}
.view button{border:0;background:none;color:var(--fg);font:inherit;font-size:13px;padding:0 12px;border-radius:8px}.view button.on{background:var(--segon);font-weight:600}
select{width:100%;margin-bottom:12px}
h2{font-size:21px;margin:22px 0 10px;letter-spacing:-.2px}
.card{display:flex;gap:14px;align-items:center;background:var(--card);border-radius:20px;padding:12px;margin-bottom:12px;color:inherit}
.cover{width:104px;height:104px;border-radius:12px;flex:none;object-fit:cover;background:linear-gradient(135deg,#4a4f5a,#23262c);box-shadow:0 2px 8px rgba(0,0,0,.25)}
.meta{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}
.t{font-size:18px;font-weight:700;line-height:1.2;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.a{font-size:15px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.d{font-size:14px;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:3px;min-width:0}.chip{font-size:12px;padding:2px 9px;border-radius:99px;font-weight:600;white-space:nowrap;flex:none}
.chip.genre{background:var(--seg);color:var(--muted);flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis}
.Calme{background:var(--calme-bg);color:var(--calme)}.Groove{background:var(--groove-bg);color:var(--groove)}.Énergique{background:var(--ener-bg);color:var(--ener)}
.acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}.acts a{font-size:13px;font-weight:600;color:var(--accent);background:var(--seg);border-radius:99px;padding:5px 12px;text-decoration:none}
a.cvl{display:block}.cvw{position:relative;flex:none;border-radius:12px;overflow:hidden;line-height:0}.grid .cvw{width:100%}
.ribbon{position:absolute;left:0;top:0;background:#fff;color:#14161a;font-size:10px;font-weight:800;line-height:1.3;padding:2px 8px;border-bottom-right-radius:8px;letter-spacing:.5px;box-shadow:0 0 0 1px rgba(0,0,0,.3)}
.chip.exc{background:var(--exc-bg);color:var(--exc)}
.chip.abs{background:var(--abs-bg);color:var(--abs)}
.ribbon.abs{background:var(--abs-ribbon);color:#fff;box-shadow:none}
.card{position:relative;overflow:hidden}.card.abs{padding-left:20px}.card.abs:before{content:"";position:absolute;left:0;top:0;bottom:0;width:6px;background:var(--abs-ribbon)}
.ex .t{font-style:italic;font-weight:600}.ex{opacity:.78}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.grid .card{flex-direction:column;align-items:stretch;gap:8px;padding:10px;margin:0;min-width:0}.grid .meta{width:100%}.grid .acts{flex-wrap:wrap}
.grid .cover{width:100%;height:auto;aspect-ratio:1}.grid .t{font-size:15px}.grid .a,.grid .d{font-size:13px}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}.kpi{background:var(--card);border-radius:16px;padding:14px}
.kpi b{display:block;font-size:30px;font-variant-numeric:tabular-nums}.kpi span{font-size:13px;color:var(--muted)}
.box{background:var(--card);border-radius:16px;padding:14px;margin-bottom:12px}.box h3{margin:0 0 10px;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.cols{display:flex;align-items:flex-end;gap:6px;height:130px;overflow-x:auto}.cols div{flex:1 0 28px;display:flex;flex-direction:column;align-items:center;gap:3px;font-size:10px;color:var(--muted);height:100%;justify-content:flex-end}
.cols i{display:block;width:100%;background:var(--accent);border-radius:4px 4px 0 0;min-height:2px}.cols em{font-style:normal;color:var(--fg);font-size:11px}
.hb{display:grid;grid-template-columns:100px 1fr 30px;gap:8px;align-items:center;font-size:14px;margin:8px 0}.hb .n{text-align:right;font-variant-numeric:tabular-nums}
.hb .tr{height:10px;border-radius:5px;background:var(--line);overflow:hidden}.hb .tr i{display:block;height:100%;border-radius:5px;background:var(--accent)}
.hb .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.avg{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}.avg b{display:block;font-size:21px;font-variant-numeric:tabular-nums}.avg span{font-size:12px;color:var(--muted)}
.tl{display:grid;grid-template-columns:76px 1fr 98px;gap:8px;align-items:center;font-size:13px;margin:8px 0}.tl .rg{position:relative;height:10px;border-radius:5px;background:var(--line)}
.tl .rg i{position:absolute;top:0;bottom:0;border-radius:5px;background:var(--accent)}.tl .tm{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted)}.tl.tap{cursor:pointer}
.axis{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin:0 98px 0 84px}.note{font-size:12px;color:var(--muted);margin:10px 0 0}
.shrow{display:flex;justify-content:space-between;font-size:15px;margin:6px 0}.shrow span{color:var(--muted)}.shrow b{font-variant-numeric:tabular-nums}
.box .note{margin:8px 0 0;font-size:12px;color:var(--muted)}
.a.lnk{cursor:pointer;color:var(--accent)}
.back{border:0;background:none;color:var(--accent);font:inherit;font-size:16px;padding:6px 0;margin:0 0 6px}
.ahead{display:flex;gap:14px;align-items:center;margin-bottom:14px}
.aimg{width:96px;height:96px;border-radius:50%;object-fit:cover;background:var(--seg);flex:none}
.ahead h2{margin:0;font-size:26px;letter-spacing:-.3px}.ahead .d{margin-top:2px}
.box .bio p{margin:0 0 10px;line-height:1.45}.box .src{font-size:12px;color:var(--muted);margin-top:4px}
.alb{display:grid;grid-template-columns:48px 1fr;gap:4px 10px;font-size:15px}.alb b{color:var(--muted);font-weight:500;font-variant-numeric:tabular-nums}
.tags{display:flex;flex-wrap:wrap;gap:8px}.tags span,.tags button{font:inherit;font-size:14px;border:0;border-radius:99px;padding:6px 12px;background:var(--seg);color:var(--fg)}
.tags button{color:var(--accent);font-weight:600}
.heard{font-size:14px;margin:6px 0;display:flex;justify-content:space-between;gap:10px}.heard span:last-child{color:var(--muted);white-space:nowrap}
.ver{color:var(--muted);font-size:12px;text-align:center;margin:28px 0 0}
.empty{color:var(--muted);padding:28px 4px;text-align:center}
</style></head><body>
<h1>Radio Soirées</h1><p class="sub" id="sub"></p><div id="warn"></div>
<div class="seg"><button data-tab="biblio" class="on">Bibliothèque</button><button data-tab="soiree">Soirée</button><button data-tab="stats">Stats</button><button data-tab="absents">Absents</button></div>
<main id="main"></main>
<p class="ver" id="ver"></p>
<script>
const D=${json};
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e};
const pp=(d)=>{const o={};for(const p of new Intl.DateTimeFormat('fr-FR',{timeZone:'Europe/Paris',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(d))o[p.type]=p.value;return o};
const evOf=(d)=>{const p=pp(new Date(d.getTime()-216e5));return p.year+'-'+p.month+'-'+p.day};
const U=(s)=>new Date(s+'T12:00:00Z');
const fmtEv=(s,o)=>new Intl.DateTimeFormat('fr-FR',Object.assign({timeZone:'UTC'},o||{weekday:'long',day:'numeric',month:'long'})).format(U(s));
const H=D.history.map(r=>{const d=new Date(r.start);const p=pp(d);return Object.assign({},r,{d,ev:evOf(d),time:p.hour+'h'+p.minute})}).sort((a,b)=>a.d-b.d);
const T=H.filter(r=>r.status!=='Excerpt');
// Tracks absent from the music service: the "missing" database, with the detection time (and cover, mood)
// taken from the matching history row when there is one.
const nk=(x)=>(x||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9]+/g,' ').trim();
const hk=new Map();for(const r of H)if(!r.link||r.status==='Not found'||r.status==='Not on Qobuz')hk.set(nk(r.artist)+'|'+nk(r.title),r);
const A=D.missing.filter(x=>x.title).map(x=>{const h=hk.get(nk(x.artist)+'|'+nk(x.title));
  const day=(x.date||'').slice(0,10);
  if(h)return Object.assign({},h,{cover:x.cover||h.cover,album:x.album||h.album,year:x.year||h.year,genre:x.genre||h.genre,shazam:x.shazam||h.shazam,youtube:x.youtube||h.youtube,link:'',albumLink:'',dayPlaylist:'',monthPlaylist:'',status:'Not found'});
  const d=day?new Date(day+'T12:00:00Z'):new Date(0);
  return Object.assign({},x,{d,ev:day||'1970-01-01',noTime:true,link:'',status:'Not found',mood:'',position:''})}).sort((a,b)=>b.d-a.d);
const evs=[...new Set(H.map(r=>r.ev))].sort().reverse();
const evsT=[...new Set(T.map(r=>r.ev))].sort().reverse();   // evenings of the statistics: short excerpts left out
const todayEv=evOf(new Date());
const SH=new Map((D.shows||[]).map(x=>[x.date.slice(0,10),x]));
const clk=(iso)=>new Intl.DateTimeFormat('fr-FR',{timeZone:'Europe/Paris',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(new Date(iso));
const cap=(x)=>x.charAt(0).toUpperCase()+x.slice(1);
const dayDiff=(ev)=>Math.round((U(todayEv)-U(ev))/864e5);
const monthName=(ev)=>cap(new Intl.DateTimeFormat('fr-FR',{timeZone:'UTC',month:'long',year:'numeric'}).format(U(ev)));
const groupLabel=(ev)=>{const d=dayDiff(ev);
  if(d<=0)return 'Aujourd\\'hui';if(d===1)return 'Hier';
  if(d<=6)return cap(new Intl.DateTimeFormat('fr-FR',{timeZone:'UTC',weekday:'long'}).format(U(ev)));
  if(d<=13)return 'La semaine dernière';
  const y=+ev.slice(0,4),m=+ev.slice(5,7),ty=+todayEv.slice(0,4),tm=+todayEv.slice(5,7);
  if(y*12+m===ty*12+tm-1)return 'Le mois dernier';
  if(y===ty&&m===tm)return 'Mois en cours';
  if(y===ty)return monthName(ev);
  return String(y)};
let cur=evs[0],tab='biblio',layout='list',query='',view=null,artist=null,trail=[];
const AR=new Map();for(const x of (D.artists||[]))AR.set(nk(x.name),x);
const firstName=(n)=>(n||'').split(/\\s+(?:&|feat\\.?|ft\\.?|featuring|x|with|and)\\s+|,\\s*/i)[0];
const findArtist=(n)=>AR.get(nk(n))||AR.get(nk(firstName(n)));
function openArtist(n){if(view==='artist')trail.push(artist);artist=n;view='artist';render();window.scrollTo(0,0)}
function back(){if(trail.length)artist=trail.pop();else view=null;render();window.scrollTo(0,0)}
function renderArtist(M){
  const b=el('button','back','‹ Retour');b.onclick=back;M.append(b);
  const x=findArtist(artist);const rows=H.filter(r=>nk(r.artist)===nk(artist)||nk(firstName(r.artist))===nk(firstName(artist))).sort((a,c)=>c.d-a.d);
  const head=el('div','ahead');const img=(x&&x.image)?el('img','aimg'):el('div','aimg');if(x&&x.image){img.src=x.image;img.alt=''}head.append(img);
  const hm=el('div');hm.append(el('h2',null,x?x.name:artist));const cs=[x&&x.country,rows.length+' titre'+(rows.length>1?'s':'')+' entendu'+(rows.length>1?'s':'')].filter(Boolean).join(' · ');hm.append(el('div','d',cs));head.append(hm);M.append(head);
  if(!x){const c=el('div','box');c.append(el('p','note','La fiche de cet artiste sera ajoutée automatiquement demain matin.'));M.append(c)}
  if(x&&x.bio){const c=el('div','box');c.append(el('h3',null,'Biographie'));const bio=el('div','bio');for(const p of x.bio.split(/\\n\\s*\\n|\\n/).filter(Boolean))bio.append(el('p',null,p));c.append(bio);if(x.source)c.append(el('div','src',x.source));M.append(c)}
  if(x&&x.albums){const c=el('div','box');c.append(el('h3',null,'Albums'));const g=el('div','alb');for(const l of x.albums.split('\\n').filter(Boolean)){const m=/^(\\S+) · (.*)$/.exec(l);g.append(el('b',null,m?m[1]:''),el('span',null,m?m[2]:l))}c.append(g);M.append(c)}
  if(x&&x.influences){const c=el('div','box');c.append(el('h3',null,'Influences'));const t=el('div','tags');for(const n of x.influences.split(/,\\s*/).filter(Boolean)){const f=findArtist(n);if(f){const bt=el('button',null,n);bt.onclick=()=>openArtist(f.name);t.append(bt)}else t.append(el('span',null,n))}c.append(t);M.append(c)}
  if(rows.length){const c=el('div','box');c.append(el('h3',null,'Titres entendus'));for(const r of rows.slice(0,30)){const d=el('div','heard');d.append(el('span',null,r.title),el('span',null,new Intl.DateTimeFormat('fr-FR',{timeZone:'Europe/Paris',day:'numeric',month:'short'}).format(r.d)));c.append(d)}M.append(c)}
  if(x&&x.similar){const c=el('div','box');c.append(el('h3',null,'Artistes similaires'));const t=el('div','tags');for(const n of x.similar.split(/,\\s*/).filter(Boolean)){const f=findArtist(n);if(f){const bt=el('button',null,n);bt.onclick=()=>openArtist(f.name);t.append(bt)}else t.append(el('span',null,n))}c.append(t);M.append(c)}
}
const nT=(ev)=>T.filter(r=>r.ev===ev).length;
document.getElementById('ver').textContent='Version '+(D.version||'?');
document.getElementById('sub').textContent=H.length?(T.length+' titres sur '+evsT.length+' soirées'):'Aucune donnée';
const W=document.getElementById('warn');
if(D.ci&&!D.ci.ok){const w=el('div','warn bad','⚠️ La dernière exécution du workflow a échoué. ');const a=el('a',null,'Voir');a.href=D.ci.url;w.append(a);W.append(w)}
if(D.offline)W.append(el('div','warn',D.offline));
function dateLine(r){const x=new Intl.DateTimeFormat('fr-FR',{timeZone:r.noTime?'UTC':'Europe/Paris',weekday:'short',day:'numeric',month:'short'}).format(r.d);return r.noTime?x:x+' à '+r.time}
function card(r){
  // Absent from the music service (short excerpts keep their own marker).
  const absent=r.status!=='Excerpt'&&(!r.link||r.status==='Not found'||r.status==='Not on Qobuz');
  const a=el('div','card'+(r.status==='Excerpt'?' ex':'')+(absent?' abs':''));
  // Track not on the music service: no playlist button. Otherwise the daily playlist, else the monthly one.
  const onService=!!r.link&&r.status!=='Not found'&&r.status!=='Not on Qobuz';
  const list=onService?(r.dayPlaylist||r.monthPlaylist):'';
  const main=list||r.link||r.shazam||r.youtube||'';
  let img;if(r.cover){img=el('img','cover');img.loading='lazy';img.alt='';img.src=r.cover}else img=el('div','cover');
  const cw=el('div','cvw');if(main){const l=el('a','cvl');l.href=main;l.append(img);cw.append(l)}else cw.append(img);
  if(r.status==='Excerpt')cw.append(el('span','ribbon','EXTRAIT'));else if(absent)cw.append(el('span','ribbon abs','ABSENT'));a.append(cw);
  const m=el('div','meta');const an=el('div','a lnk',r.artist);an.onclick=()=>openArtist(r.artist);m.append(el('div','t',r.title),an);
  const alb=[r.album,r.year].filter(Boolean).join(' · ');
  m.append(el('div','d',dateLine(r)));
  const extra=[alb,r.position].filter(Boolean).join(' · ');if(extra)m.append(el('div','d',extra));
  if(r.genre||r.mood||r.status==='Excerpt'||absent){const c=el('div','chips');if(r.status==='Excerpt')c.append(el('span','chip exc','✂️ Extrait'));if(absent)c.append(el('span','chip abs','⛔ Absent de Qobuz'));if(r.genre)c.append(el('span','chip genre',r.genre));if(r.mood)c.append(el('span','chip '+r.mood,r.mood));m.append(c)}
  const acts=el('div','acts');
  if(list){const x=el('a',null,r.dayPlaylist?'▶ Playlist du jour':'▶ Playlist du mois');x.href=list;acts.append(x)}
  // The track link opens the album, like the Album button: only kept when there is no album link.
  if(r.link&&!r.albumLink){const x=el('a',null,list?'Titre':'▶ Titre');x.href=r.link;acts.append(x)}
  if(r.albumLink){const x=el('a',null,'Album');x.href=r.albumLink;acts.append(x)}
  if(!r.link&&r.shazam){const x=el('a',null,'Shazam');x.href=r.shazam;acts.append(x)}
  {const x=el('a',null,'YouTube');x.href=r.youtube||('https://www.youtube.com/results?search_query='+encodeURIComponent(r.artist+' '+r.title));acts.append(x)}
  if(acts.children.length)m.append(acts);
  a.append(m);return a}
function groups(rows){const g=new Map();for(const r of rows){const k=groupLabel(r.ev);if(!g.has(k))g.set(k,[]);g.get(k).push(r)}return g}
function bars(title,items,color,note){const c=el('div','box');c.append(el('h3',null,title));if(!items.length)c.append(el('p','note','Aucune donnée.'));const mx=Math.max(1,...items.map(x=>x[1]));
  for(const [k,v] of items){const b=el('div','hb');const tr=el('div','tr');const i=el('i');i.style.width=(100*v/mx)+'%';if(color)i.style.background=color(k);tr.append(i);b.append(el('span','name',k),tr,el('span','n',String(v)));c.append(b)}if(note)c.append(el('p','note',note));return c}
const count=(arr,f)=>{const m=new Map();for(const r of arr){const k=f(r);if(k)m.set(k,(m.get(k)||0)+1)}return [...m]};
function render(){const M=document.getElementById('main');M.replaceChildren();
  if(view==='artist'){renderArtist(M);return}
  if(tab==='biblio'){
    const bar=el('div','bar');const i=el('input');i.type='search';i.placeholder='Rechercher un titre, un artiste…';i.value=query;i.id='q';
    const v=el('div','view');for(const [k,l] of [['list','Liste'],['grid','Grille']]){const b=el('button',layout===k?'on':'',l);b.onclick=()=>{layout=k;render()};v.append(b)}
    bar.append(i,v);M.append(bar);const list=el('div');M.append(list);
    const fill=()=>{list.replaceChildren();const q=query.trim().toLowerCase();
      const rows=H.filter(r=>!q||(r.title+' '+r.artist+' '+r.album).toLowerCase().includes(q)).sort((a,b)=>b.d-a.d);
      if(!rows.length){list.append(el('div','empty',q?'Aucun résultat.':'Aucun titre pour l\\'instant.'));return}
      for(const [label,items] of groups(rows)){list.append(el('h2',null,label));const box=el('div',layout==='grid'?'grid':'');for(const r of items)box.append(card(r));list.append(box)}};
    i.oninput=()=>{query=i.value;fill()};fill()}
  if(tab==='soiree'){if(!evs.length){M.append(el('div','empty','Aucun titre pour l\\'instant.'));return}
    const s=el('select');for(const e of evs){const o=el('option',null,fmtEv(e)+' · '+nT(e)+' titres');o.value=e;s.append(o)}s.value=cur;s.onchange=()=>{cur=s.value;render()};M.append(s);
    const sh=SH.get(cur);
    if(sh){const b=el('div','box');b.append(el('h3',null,'Émission'));
      const row=(l,v)=>{const d=el('div','shrow');d.append(el('span',null,l),el('b',null,v));b.append(d)};
      row('Début',clk(sh.start));if(sh.startDetected)row('Début détecté',clk(sh.startDetected));
      if(sh.end){row('Fin',clk(sh.end));if(sh.endDetected)row('Fin détectée',clk(sh.endDetected));else row('Fin détectée','non détectée (limite de temps)')}
      else row('Fin','en cours');M.append(b)}
    for(const r of H.filter(r=>r.ev===cur))M.append(card(r))}
  if(tab==='stats'){const k=el('div','kpis');const kpi=(n,l)=>{const d=el('div','kpi');d.append(el('b',null,String(n)),el('span',null,l));k.append(d)};
    kpi(T.length,'titres retenus');kpi(evsT.length,'soirées');kpi(new Set(T.map(r=>r.artist.toLowerCase())).size,'artistes');kpi(D.missing.length,'absents du service');M.append(k);
    const c=el('div','box');c.append(el('h3',null,'Titres par soirée'));const cols=el('div','cols');const last=evsT.slice(0,14).reverse();const mx=Math.max(1,...last.map(nT));
    for(const e of last){const d=el('div');const i=el('i');i.style.height=(85*nT(e)/mx)+'%';d.append(el('em',null,String(nT(e))),i,el('span',null,fmtEv(e,{day:'2-digit',month:'2-digit'})));d.onclick=()=>{cur=e;tab='soiree';setTab()};cols.append(d)}c.append(cols);M.append(c);
    // Detected start and end of the show, per evening: first track kept (the show starts with it)
    // and end of the last track kept (the tracks after the detected end are not kept).
    const mOf=(d)=>{const q=pp(d);let m=(+q.hour)*60+(+q.minute);if(m<720)m+=1440;return m};
    // Tracks played outside the show (tests in the daytime...) are ignored for the estimated times:
    // the show is between 21h00 (music before it is ignored too) and 02h30.
    const inShow=(d)=>{const m=mOf(d);return m>=1260&&m<=1590};
    const hh=(m)=>{const x=((Math.round(m)%1440)+1440)%1440;return String(Math.floor(x/60)).padStart(2,'0')+'h'+String(x%60).padStart(2,'0')};
    const allEv=[...new Set([...evsT,...SH.keys()])].sort().reverse();
    const spans=allEv.map(ev=>{const sh=SH.get(ev);
      if(sh&&sh.start){const s0=mOf(new Date(sh.start));
        if(sh.end)return {ev,s:s0,e:Math.max(mOf(new Date(sh.end)),s0),exact:true,live:false};
        if(ev===todayEv)return {ev,s:s0,e:s0,exact:true,live:true}}
      const r=T.filter(x=>x.ev===ev&&inShow(x.d));if(r.length<3)return null;const a=r[0],b=r[r.length-1];
      const endD=new Date(b.d.getTime()+(b.duration||0)*1000);const s=mOf(a.d);return {ev,s,e:Math.max(mOf(endD),s),exact:false,live:ev===todayEv&&Date.now()-endD.getTime()<1500000}}).filter(Boolean);
    if(spans.length){const done=spans.filter(x=>!x.live);const avg=(f)=>done.reduce((t,x)=>t+f(x),0)/Math.max(1,done.length);
      const hb=el('div','box');hb.append(el('h3',null,'Horaires détectés'));
      if(done.length){const a=el('div','avg');const av=(v,l)=>{const d=el('div');d.append(el('b',null,v),el('span',null,l));a.append(d)};
        const dm=avg(x=>x.e-x.s);av(hh(avg(x=>x.s)),'début moyen');av(hh(avg(x=>x.e)),'fin moyenne');av(Math.floor(dm/60)+' h '+String(Math.round(dm%60)).padStart(2,'0'),'durée moyenne');hb.append(a)}
      const shown=spans.slice(0,14);const lo=Math.floor(Math.min(...shown.map(x=>x.s))/30)*30,hi=Math.ceil(Math.max(...shown.map(x=>x.e))/30)*30;
      const ax=el('div','axis');ax.append(el('span',null,hh(lo)),el('span',null,hh(hi)));hb.append(ax);
      for(const x of shown){const t=el('div','tl tap');const rg=el('div','rg');const i=el('i');i.style.left=(100*(x.s-lo)/(hi-lo))+'%';i.style.width=Math.max(1.5,100*(x.e-x.s)/(hi-lo))+'%';rg.append(i);
        t.append(el('span',null,fmtEv(x.ev,{weekday:'short',day:'numeric',month:'short'})),rg,el('span','tm',(x.exact?'':'~')+hh(x.s)+'–'+(x.live?'…':hh(x.e))));
        t.onclick=()=>{cur=x.ev;tab='soiree';setTab()};hb.append(t)}
      hb.append(el('p','note','Heures relevées par l\\'écoute (début et fin de l\\'émission). ~ : estimées d\\'après les titres, pour les soirées sans relevé.'));M.append(hb)}
    const mc={Calme:'var(--calme)',Groove:'var(--groove)','Énergique':'var(--ener)'};
    M.append(bars('Ambiances',['Calme','Groove','Énergique'].map(x=>[x,T.filter(r=>r.mood===x).length]),k=>mc[k]));
    M.append(bars('Décennies',count(T,r=>r.decade).sort((a,b)=>parseInt(a[0])-parseInt(b[0]))));
    M.append(bars('Artistes les plus diffusés',count(T,r=>r.artist).sort((a,b)=>b[1]-a[1]).slice(0,8)));
    M.append(bars('Genres',count(T,r=>r.genre).sort((a,b)=>b[1]-a[1]).slice(0,8)));
    const top=(f)=>count(T,f).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,10);
    const unk=(f)=>{const n=T.filter(r=>!f(r)).length;return n?n+' titre'+(n>1?'s':'')+' sans information.':''};
    M.append(bars('Labels (maisons de disques)',top(r=>r.label),null,unk(r=>r.label)));
    M.append(bars('Pays des artistes',top(r=>r.country),null,unk(r=>r.country)))}
  if(tab==='absents'){if(!A.length){M.append(el('div','empty','Aucun titre absent.'));return}
    for(const [label,items] of groups(A)){M.append(el('h2',null,label));for(const r of items)M.append(card(r))}}}
function setTab(){view=null;trail=[];document.querySelectorAll('.seg button').forEach(b=>b.classList.toggle('on',b.dataset.tab===tab));render();window.scrollTo(0,0)}
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;setTab()});
render();
if(D.open)openArtist(D.open);
</script></body></html>`;
}

async function app(data) {
  if (!data) {
    const a = new Alert(); a.title = "Radio Soirées"; a.message = "Aucune clé Notion enregistrée."; a.addAction("OK"); await a.present();
    return;
  }
  const wv = new WebView();
  wv.shouldAllowRequest = (req) => {
    const u = req.url || "";
    if (/^https?:/.test(u)) { Safari.open(u); return false; }
    return true;
  };
  data.version = VERSION;
  // Opened from a notification: scriptable:///run/<script>?artist=<name> shows that artist's page.
  data.open = (args.queryParameters && args.queryParameters.artist) || "";
  await wv.loadHTML(html(data));
  await wv.present(true);
}

// ---------- Main ----------
const reset = args.queryParameters && args.queryParameters.reset;
if (reset) { if (Keychain.contains(KEY)) Keychain.remove(KEY); }
let data = null, error = null;
try { data = await loadData(); } catch (e) { error = e; }
if (config.runsInWidget) {
  const w = await widget(data);
  if (error) { const t = w.addText("Erreur de chargement"); t.textColor = Color.red(); t.font = Font.systemFont(10); }
  Script.setWidget(w);
} else if (error) {
  const a = new Alert(); a.title = "Radio Soirées";
  a.message = error.code === 404
    ? "Notion ne trouve pas les bases : ajoute la connexion en lecture seule aux deux bases (menu ••• › Connexions)."
    : `Chargement impossible : ${error.message}`;
  a.addAction("OK"); await a.present();
} else {
  await app(data);
}
Script.complete();
