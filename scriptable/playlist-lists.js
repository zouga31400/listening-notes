// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: purple; icon-glyph: list-ul;
//
// Playlist Lists — Scriptable app and widget (read-only view of the "Playlist lists" Notion
// database: workflow "Notion lists sync" of zouga31400/listening-notes).
// Same look as the Radio Soirées app. Tabs: Bibliothèque (every playlist, searchable),
// À suivre (waiting, in error, tracks not found), Films, Stats. Tap a cover to open the
// playlist in the music app; "Notion" opens the page (to correct a line and set the
// Status back to "To create").
//
// Reads Notion with a READ-ONLY connection. Its key is kept in this device's keychain
// (asked once); it never appears in this file. The connection must be added to the
// "Playlist lists" database (or to its parent page "Musique": ••• › Connexions).
// Widget: add a Scriptable widget to the home screen and choose this script.

const KEY = "playlist_lists_notion_key";
const LISTS_DB = "ea8e66660faa4ce299e2f599916f1058";
const WORKFLOW = "notion-lists.yml";
const API = "https://api.notion.com/v1";
const FM = FileManager.local();
const CACHE = FM.joinPath(FM.cacheDirectory(), "playlist-lists.json");

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
    case "rich_text": return p.rich_text.map((t) => t.plain_text).join("");
    case "select": return p.select ? p.select.name : "";
    case "number": return p.number;
    case "url": return p.url || "";
    case "created_time": return p.created_time || null;
    default: return null;
  }
}

function coverOf(pg) {
  const c = pg.cover;
  if (!c) return "";
  return c.type === "external" ? (c.external && c.external.url) || "" : (c.file && c.file.url) || "";
}

async function queryAll(key, maxPages) {
  const rows = [];
  let cursor;
  for (let i = 0; i < maxPages; i++) {
    const r = new Request(`${API}/databases/${LISTS_DB}/query`);
    r.method = "POST";
    r.headers = {"Authorization": `Bearer ${key}`, "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"};
    const body = {page_size: 100, sorts: [{timestamp: "created_time", direction: "descending"}]};
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

// Last run of the sync workflow that did something (runs with nothing to do delete
// themselves from the history). Public repository: no key needed.
async function ciStatus() {
  try {
    const r = new Request(`https://api.github.com/repos/zouga31400/listening-notes/actions/workflows/${WORKFLOW}/runs?per_page=10&status=completed`);
    r.headers = {"Accept": "application/vnd.github+json"};
    r.timeoutInterval = 10;
    const j = await r.loadJSON();
    if (r.response.statusCode !== 200) return null;
    const run = (j.workflow_runs || [])[0];
    if (!run) return null;
    const recent = Date.now() - new Date(run.updated_at).getTime() < 12 * 3600e3;
    return {id: run.id, ok: !(recent && (run.conclusion === "failure" || run.conclusion === "timed_out")),
            conclusion: run.conclusion, at: run.updated_at, url: run.html_url};
  } catch (e) { return null; }
}

function notifyFailure(ci) {
  // Best effort: iOS decides when widgets refresh, so this is not a reliable alarm.
  try {
    const f = FM.joinPath(FM.cacheDirectory(), "playlist-lists-ci.txt");
    const seen = FM.fileExists(f) ? FM.readString(f) : "";
    if (ci && !ci.ok && String(ci.id) !== seen) {
      const n = new Notification();
      n.title = "Playlist Lists";
      n.body = "La dernière synchronisation des playlists a échoué.";
      n.openURL = ci.url;
      n.schedule();
      FM.writeString(f, String(ci.id));
    } else if (ci && ci.ok && seen) { FM.writeString(f, ""); }
  } catch (e) {}
}

async function loadData() {
  let key = await getKey();
  if (!key) return null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const pages = await queryAll(key, config.runsInWidget ? 1 : 10);
      const lists = pages.map((pg) => {
        const p = pg.properties;
        return {
          id: pg.id, url: pg.url || "", created: prop(p["Created on"]) || pg.created_time,
          title: prop(p.Name) || "(sans nom)", status: prop(p.Status) || "",
          added: prop(p.Added) || 0, notFound: prop(p["Not found"]) || 0,
          link: prop(p.Link) || "", film: prop(p.Film) || "", author: prop(p.Author) || "",
          cover: coverOf(pg),
        };
      }).filter((r) => r.created);
      const ci = await ciStatus();
      notifyFailure(ci);
      const data = {lists, ci, at: new Date().toISOString()};
      if (!config.runsInWidget) FM.writeString(CACHE, JSON.stringify(data));
      return data;
    } catch (e) {
      if (e.code === 401 && !config.runsInWidget && attempt === 0) {
        Keychain.remove(KEY);
        key = await askKey(true);
        if (!key) return null;
        continue;
      }
      if (FM.fileExists(CACHE)) {
        const d = JSON.parse(FM.readString(CACHE));
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
const dayOf = (d) => { const p = parisParts(d); return `${p.year}-${p.month}-${p.day}`; };
const timeOf = (d) => { const p = parisParts(d); return `${p.hour}h${p.minute}`; };
function dayLabel(day) {
  if (day === dayOf(new Date())) return "Aujourd'hui";
  if (day === dayOf(new Date(Date.now() - 864e5))) return "Hier";
  try { return new Intl.DateTimeFormat("fr-FR", {timeZone: "UTC", weekday: "short", day: "numeric", month: "short"}).format(new Date(day + "T12:00:00Z")); }
  catch (e) { return day; }
}
const plural = (n, w) => `${n} ${w}${n > 1 ? "s" : ""}`;

// ---------- Widget ----------
async function image(url) {
  if (!url) return null;
  try { const r = new Request(url); r.timeoutInterval = 10; return await r.loadImage(); } catch (e) { return null; }
}

const STATUS_LABEL = {"To create": "En attente", "Created": "Créée", "Album": "Album", "Error": "Erreur"};
const STATUS_COLOR = {"To create": "#7fb3ff", "Created": "#7fd99a", "Album": "#c9a6ff", "Error": "#ff8a80"};

async function widget(data) {
  const w = new ListWidget();
  w.backgroundColor = new Color("#16181d");
  w.refreshAfterDate = new Date(Date.now() + 15 * 60e3);
  const fam = config.widgetFamily || "medium";
  const white = Color.white(), grey = new Color("#a7acb5");
  if (!data) { const t = w.addText("Ouvre l'app une fois pour saisir la clé Notion."); t.textColor = white; t.font = Font.systemFont(12); return w; }
  const lists = data.lists.filter((r) => r.status !== "To create");
  const waiting = data.lists.filter((r) => r.status === "To create").length;
  if (!data.lists.length) { const t = w.addText("Aucune playlist pour l'instant."); t.textColor = white; return w; }
  const last = lists[0] || data.lists[0];
  const label = `${dayLabel(dayOf(new Date(last.created))).toUpperCase()} · ${plural(lists.length, "PLAYLIST")}`;
  const warn = data.ci && !data.ci.ok ? "⚠️ Synchronisation en échec"
    : (waiting ? `⏳ ${plural(waiting, "playlist")} en attente` : "");

  if (fam === "small") {
    const img = await image(last.cover);
    if (img) { w.backgroundImage = img; w.backgroundGradient = null; }
    const g = new LinearGradient(); g.colors = [new Color("#000000", 0.05), new Color("#000000", 0.78)]; g.locations = [0.3, 1];
    w.backgroundGradient = g;
    w.addSpacer();
    if (warn) { const f = w.addText(warn); f.font = Font.boldSystemFont(9); f.textColor = new Color("#ff8a80"); }
    const l = w.addText(label); l.font = Font.boldSystemFont(9); l.textColor = new Color("#e4e6ea");
    const t = w.addText(last.title); t.font = Font.boldSystemFont(13); t.textColor = white; t.lineLimit = 2;
    const a = w.addText(`${STATUS_LABEL[last.status] || last.status} · ${plural(last.added, "titre")}`);
    a.font = Font.systemFont(10); a.textColor = new Color("#e4e6ea"); a.lineLimit = 1;
    return w;
  }

  if (warn) { const f = w.addText(warn); f.font = Font.boldSystemFont(10); f.textColor = new Color("#ff8a80"); w.addSpacer(2); }
  const head = w.addText(label); head.font = Font.boldSystemFont(10); head.textColor = grey;
  w.addSpacer(6);
  const n = fam === "large" ? 6 : 1;
  for (const r of lists.slice(0, n)) {
    const row = w.addStack(); row.centerAlignContent();
    const img = await image(r.cover);
    const size = fam === "large" ? 44 : 100;
    if (img) { const i = row.addImage(img); i.imageSize = new Size(size, size); i.cornerRadius = 8; }
    else { const s = row.addStack(); s.size = new Size(size, size); s.backgroundColor = new Color("#2a2d33"); s.cornerRadius = 8; }
    row.addSpacer(10);
    const col = row.addStack(); col.layoutVertically();
    const t = col.addText(r.title); t.font = Font.boldSystemFont(fam === "large" ? 13 : 15); t.textColor = white; t.lineLimit = 2;
    const a = col.addText(r.film ? `🎬 ${r.film}` : (r.author ? `par ${r.author}` : "")); a.font = Font.systemFont(12); a.textColor = new Color("#c9ccd2"); a.lineLimit = 1;
    const m = col.addText([STATUS_LABEL[r.status] || r.status, plural(r.added, "titre"),
      r.notFound ? `${r.notFound} absent${r.notFound > 1 ? "s" : ""}` : "", timeOf(new Date(r.created))].filter(Boolean).join(" · "));
    m.font = Font.systemFont(11); m.textColor = new Color(STATUS_COLOR[r.status] || "#a7acb5");
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
--wait-bg:#dcecfb;--wait:#1d5f99;--ok-bg:#d9f2e0;--ok:#1d6b35;--alb-bg:#eadcfb;--alb:#6a2fb0;--err-bg:#fbdcdc;--err:#9b2222;--warn-bg:#fbefcc;--warn:#7a5800}
@media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1f;--fg:#f5f5f7;--muted:#9a9aa2;--line:#2b2b30;--accent:#7fb3ff;--seg:#1f1f23;--segon:#3a3a40;
--wait-bg:#173a52;--wait:#7cc4ff;--ok-bg:#16402392;--ok:#7fd99a;--alb-bg:#3a2552;--alb:#c9a6ff;--err-bg:#4d1d1d;--err:#ff8a80;--warn-bg:#4a3a12;--warn:#ffd166}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--fg);font:16px -apple-system,system-ui,sans-serif}
body{padding:calc(env(safe-area-inset-top) + 8px) max(16px,env(safe-area-inset-right)) calc(env(safe-area-inset-bottom) + 28px) max(16px,env(safe-area-inset-left))}
h1{font-size:34px;margin:6px 0 2px;letter-spacing:-.5px}.sub{color:var(--muted);margin:0 0 14px;font-size:15px}
.warn{border-radius:12px;padding:10px 14px;font-size:14px;margin-bottom:10px;background:var(--warn-bg);color:var(--warn)}
.warn.bad{background:var(--err-bg);color:var(--err);font-weight:600}.warn a{color:inherit}
.seg{display:flex;background:var(--seg);border-radius:10px;padding:2px;margin-bottom:14px;position:sticky;top:env(safe-area-inset-top);z-index:2}
.seg button{flex:1;border:0;background:none;color:var(--fg);font:inherit;font-size:13px;padding:8px 0;border-radius:8px}
.seg button.on{background:var(--segon);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.2)}
.bar{display:flex;gap:8px;margin-bottom:10px}
select,input{font:inherit;padding:10px 12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.bar input{flex:1;min-width:0}.view{display:flex;background:var(--seg);border-radius:10px;padding:2px}
.view button{border:0;background:none;color:var(--fg);font:inherit;font-size:13px;padding:0 12px;border-radius:8px}.view button.on{background:var(--segon);font-weight:600}
h2{font-size:21px;margin:22px 0 10px;letter-spacing:-.2px}
.card{display:flex;gap:14px;align-items:center;background:var(--card);border-radius:20px;padding:12px;margin-bottom:12px;color:inherit}
.cover{width:104px;height:104px;border-radius:12px;flex:none;object-fit:cover;background:linear-gradient(135deg,#4a4f5a,#23262c);box-shadow:0 2px 8px rgba(0,0,0,.25);display:flex;align-items:center;justify-content:center;font-size:34px}
.meta{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}
.t{font-size:18px;font-weight:700;line-height:1.2;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.a{font-size:15px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.d{font-size:14px;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:3px;min-width:0}.chip{font-size:12px;padding:2px 9px;border-radius:99px;font-weight:600;white-space:nowrap;flex:none}
.chip.n{background:var(--seg);color:var(--muted)}
.st-To_create{background:var(--wait-bg);color:var(--wait)}.st-Created{background:var(--ok-bg);color:var(--ok)}.st-Album{background:var(--alb-bg);color:var(--alb)}.st-Error{background:var(--err-bg);color:var(--err)}
.chip.nf{background:var(--warn-bg);color:var(--warn)}
.acts{display:flex;gap:8px;margin-top:6px}.acts a{font-size:13px;font-weight:600;color:var(--accent);background:var(--seg);border-radius:99px;padding:5px 12px;text-decoration:none}
a.cvl{display:block;flex:none}.grid a.cvl{width:100%}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.grid .card{flex-direction:column;align-items:stretch;gap:8px;padding:10px;margin:0;min-width:0}.grid .meta{width:100%}.grid .acts{flex-wrap:wrap}
.grid .cover{width:100%;height:auto;aspect-ratio:1}.grid .t{font-size:15px}.grid .a,.grid .d{font-size:13px}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}.kpi{background:var(--card);border-radius:16px;padding:14px}
.kpi b{display:block;font-size:30px;font-variant-numeric:tabular-nums}.kpi span{font-size:13px;color:var(--muted)}
.box{background:var(--card);border-radius:16px;padding:14px;margin-bottom:12px}.box h3{margin:0 0 10px;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.cols{display:flex;align-items:flex-end;gap:6px;height:130px;overflow-x:auto}.cols div{flex:1 0 28px;display:flex;flex-direction:column;align-items:center;gap:3px;font-size:10px;color:var(--muted);height:100%;justify-content:flex-end}
.cols i{display:block;width:100%;background:var(--accent);border-radius:4px 4px 0 0;min-height:2px}.cols em{font-style:normal;color:var(--fg);font-size:11px}
.hb{display:grid;grid-template-columns:110px 1fr 30px;gap:8px;align-items:center;font-size:14px;margin:8px 0}.hb .n{text-align:right;font-variant-numeric:tabular-nums}
.hb .tr{height:10px;border-radius:5px;background:var(--line);overflow:hidden}.hb .tr i{display:block;height:100%;border-radius:5px;background:var(--accent)}
.hb .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.empty{color:var(--muted);padding:28px 4px;text-align:center}
.hint{font-size:13px;color:var(--muted);margin:-4px 2px 12px}
</style></head><body>
<h1>Playlist Lists</h1><p class="sub" id="sub"></p><div id="warn"></div>
<div class="seg"><button data-tab="biblio" class="on">Bibliothèque</button><button data-tab="suivre">À suivre</button><button data-tab="films">Films</button><button data-tab="stats">Stats</button></div>
<main id="main"></main>
<script>
const D=${json};
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e};
const pp=(d)=>{const o={};for(const p of new Intl.DateTimeFormat('fr-FR',{timeZone:'Europe/Paris',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(d))o[p.type]=p.value;return o};
const dayOf=(d)=>{const p=pp(d);return p.year+'-'+p.month+'-'+p.day};
const U=(s)=>new Date(s+'T12:00:00Z');
const L=D.lists.map(r=>{const d=new Date(r.created);const p=pp(d);return Object.assign({},r,{d,day:dayOf(d),time:p.hour+'h'+p.minute})}).sort((a,b)=>b.d-a.d);
const todayDay=dayOf(new Date());
// Older pages have no "Film" value: a name that speaks of a film, a soundtrack or a series counts too.
const isFilm=(r)=>!!r.film||r.status==='Album'||/bande originale|soundtrack|\\bost\\b|\\bfilms?\\b|série|serie tv|james bond/i.test(r.title);
const cap=(x)=>x.charAt(0).toUpperCase()+x.slice(1);
const plural=(n,w)=>n+' '+w+(n>1?'s':'');
const ST={'To create':['En attente','To_create'],'Created':['Créée','Created'],'Album':['Album','Album'],'Error':['Erreur','Error']};
const done=L.filter(r=>r.status==='Created'||r.status==='Album');
const dayDiff=(day)=>Math.round((U(todayDay)-U(day))/864e5);
const monthName=(day)=>cap(new Intl.DateTimeFormat('fr-FR',{timeZone:'UTC',month:'long',year:'numeric'}).format(U(day)));
const groupLabel=(day)=>{const d=dayDiff(day);
  if(d<=0)return 'Aujourd\\'hui';if(d===1)return 'Hier';
  if(d<=6)return cap(new Intl.DateTimeFormat('fr-FR',{timeZone:'UTC',weekday:'long'}).format(U(day)));
  if(d<=13)return 'La semaine dernière';
  const y=+day.slice(0,4),m=+day.slice(5,7),ty=+todayDay.slice(0,4),tm=+todayDay.slice(5,7);
  if(y*12+m===ty*12+tm-1)return 'Le mois dernier';
  if(y===ty&&m===tm)return 'Mois en cours';
  if(y===ty)return monthName(day);
  return String(y)};
let tab='biblio',layout='list',query='';
document.getElementById('sub').textContent=L.length?(plural(done.length,'playlist')+' · '+plural(done.reduce((s,r)=>s+(r.added||0),0),'titre')+' ajoutés'):'Aucune donnée';
const W=document.getElementById('warn');
if(D.ci&&!D.ci.ok){const w=el('div','warn bad','⚠️ La dernière synchronisation a échoué. ');const a=el('a',null,'Voir');a.href=D.ci.url;w.append(a);W.append(w)}
const stuck=L.filter(r=>r.status==='To create'&&Date.now()-r.d>15*60e3);
if(stuck.length)W.append(el('div','warn','⏳ '+plural(stuck.length,'playlist')+' en attente depuis plus de 15 min : la synchronisation ne démarre peut-être pas.'));
if(D.offline)W.append(el('div','warn',D.offline));
function dateLine(r){return new Intl.DateTimeFormat('fr-FR',{timeZone:'Europe/Paris',weekday:'short',day:'numeric',month:'short'}).format(r.d)+' à '+r.time}
function card(r){
  const a=el('div','card');
  const main=r.link||r.url||'';
  let img;if(r.cover){img=el('img','cover');img.loading='lazy';img.alt='';img.src=r.cover}else img=el('div','cover','🎧');
  if(main){const l=el('a','cvl');l.href=main;l.append(img);a.append(l)}else a.append(img);
  const m=el('div','meta');m.append(el('div','t',r.title));
  const who=r.film?'🎬 '+r.film:(r.author?'par '+r.author:'');if(who)m.append(el('div','a',who));
  if(r.film&&r.author)m.append(el('div','d','par '+r.author));
  m.append(el('div','d',dateLine(r)));
  const c=el('div','chips');const s=ST[r.status]||[r.status||'—','To_create'];c.append(el('span','chip st-'+s[1],s[0]));
  if(r.status!=='To create'&&r.status!=='Error'&&r.status!=='Album')c.append(el('span','chip n',plural(r.added||0,'titre')));
  if(r.status==='Created'&&r.notFound)c.append(el('span','chip nf',r.notFound+' absent'+(r.notFound>1?'s':'')));
  m.append(c);
  const acts=el('div','acts');
  if(r.link){const x=el('a',null,r.status==='Album'?'▶ Album':'▶ Playlist');x.href=r.link;acts.append(x)}
  if(r.url){const x=el('a',null,'Notion');x.href=r.url;acts.append(x)}
  if(acts.children.length)m.append(acts);
  a.append(m);return a}
function groups(rows){const g=new Map();for(const r of rows){const k=groupLabel(r.day);if(!g.has(k))g.set(k,[]);g.get(k).push(r)}return g}
function bars(title,items,color){const c=el('div','box');c.append(el('h3',null,title));if(!items.length){c.append(el('div','d','—'));return c}
  const mx=Math.max(1,...items.map(x=>x[1]));
  for(const [k,v] of items){const b=el('div','hb');const tr=el('div','tr');const i=el('i');i.style.width=(100*v/mx)+'%';if(color)i.style.background=color(k);tr.append(i);b.append(el('span','name',k),tr,el('span','n',String(v)));c.append(b)}return c}
const count=(arr,f)=>{const m=new Map();for(const r of arr){const k=f(r);if(k)m.set(k,(m.get(k)||0)+1)}return [...m]};
const weekStart=(day)=>{const d=U(day);d.setUTCDate(d.getUTCDate()-((d.getUTCDay()+6)%7));return d.toISOString().slice(0,10)};
function section(M,title,rows,hint){if(!rows.length)return false;M.append(el('h2',null,title));if(hint)M.append(el('div','hint',hint));for(const r of rows)M.append(card(r));return true}
function render(){const M=document.getElementById('main');M.replaceChildren();
  if(tab==='biblio'){
    const bar=el('div','bar');const i=el('input');i.type='search';i.placeholder='Rechercher…';i.value=query;i.id='q';
    const v=el('div','view');for(const [k,l] of [['list','Liste'],['grid','Grille']]){const b=el('button',layout===k?'on':'',l);b.onclick=()=>{layout=k;render()};v.append(b)}
    bar.append(i,v);M.append(bar);const list=el('div');M.append(list);
    const fill=()=>{list.replaceChildren();const q=query.trim().toLowerCase();
      const rows=L.filter(r=>!q||(r.title+' '+r.film+' '+r.author).toLowerCase().includes(q));
      if(!rows.length){list.append(el('div','empty',q?'Aucun résultat.':'Aucune playlist pour l\\'instant.'));return}
      for(const [label,items] of groups(rows)){list.append(el('h2',null,label));const box=el('div',layout==='grid'?'grid':'');for(const r of items)box.append(card(r));list.append(box)}};
    i.oninput=()=>{query=i.value;fill()};fill()}
  if(tab==='suivre'){
    const a=section(M,'En attente',L.filter(r=>r.status==='To create'),'Créées au prochain passage (toutes les 2 min).');
    const b=section(M,'En erreur',L.filter(r=>r.status==='Error'),'Ouvre la page Notion : le rapport en bas dit pourquoi. Corrige la ligne, remets le statut sur « To create ».');
    const c=section(M,'Titres introuvables',L.filter(r=>r.status==='Created'&&r.notFound>0).sort((x,y)=>y.notFound-x.notFound),'Corrige ou remplace la ligne dans Notion, puis remets le statut sur « To create ».');
    if(!(a||b||c))M.append(el('div','empty','Tout est en ordre ✅'))}
  if(tab==='films'){const rows=L.filter(isFilm);
    if(!rows.length){M.append(el('div','empty','Aucune playlist de film pour l\\'instant.'));return}
    const alb=rows.filter(r=>r.status==='Album').length;
    M.append(el('div','hint',plural(rows.length,'film')+' ou série · '+plural(alb,'album')+' trouvé'+(alb>1?'s':'')+' à la place d\\'une playlist.'));
    const box=el('div','grid');for(const r of rows)box.append(card(r));M.append(box)}
  if(tab==='stats'){const k=el('div','kpis');const kpi=(n,l)=>{const d=el('div','kpi');d.append(el('b',null,String(n)),el('span',null,l));k.append(d)};
    kpi(L.length,'listes');kpi(L.filter(r=>r.status==='Created').length,'playlists créées');
    kpi(L.reduce((s,r)=>s+(r.added||0),0),'titres ajoutés');kpi(L.reduce((s,r)=>s+(r.notFound||0),0),'titres introuvables');M.append(k);
    const c=el('div','box');c.append(el('h3',null,'Listes par semaine'));const cols=el('div','cols');
    const wk=count(L,r=>weekStart(r.day)).sort((a,b)=>a[0]<b[0]?-1:1).slice(-12);const mx=Math.max(1,...wk.map(x=>x[1]));
    for(const [w,n] of wk){const d=el('div');const i=el('i');i.style.height=(85*n/mx)+'%';d.append(el('em',null,String(n)),i,el('span',null,w.slice(8,10)+'/'+w.slice(5,7)));cols.append(d)}
    c.append(cols);M.append(c);
    const sc={'Créée':'var(--ok)','En attente':'var(--wait)','Album':'var(--alb)','Erreur':'var(--err)'};
    M.append(bars('Statuts',['To create','Created','Album','Error'].map(s=>[ST[s][0],L.filter(r=>r.status===s).length]).filter(x=>x[1]),k=>sc[k]));
    M.append(bars('Auteurs',count(L,r=>r.author||'Par défaut').sort((a,b)=>b[1]-a[1]).slice(0,8)));
    M.append(bars('Films et séries',count(L,r=>isFilm(r)?'Film / série':'Autres listes').sort((a,b)=>b[1]-a[1])))}}
function setTab(){document.querySelectorAll('.seg button').forEach(b=>b.classList.toggle('on',b.dataset.tab===tab));render();window.scrollTo(0,0)}
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;setTab()});
render();
</script></body></html>`;
}

async function app(data) {
  if (!data) {
    const a = new Alert(); a.title = "Playlist Lists"; a.message = "Aucune clé Notion enregistrée."; a.addAction("OK"); await a.present();
    return;
  }
  const wv = new WebView();
  wv.shouldAllowRequest = (req) => {
    const u = req.url || "";
    if (/^https?:/.test(u)) { Safari.open(u); return false; }
    return true;
  };
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
  const a = new Alert(); a.title = "Playlist Lists";
  a.message = error.code === 404
    ? "Notion ne trouve pas la base : ajoute la connexion en lecture seule à la base « Playlist lists » (menu ••• › Connexions)."
    : `Chargement impossible : ${error.message}`;
  a.addAction("OK"); await a.present();
} else {
  await app(data);
}
Script.complete();
