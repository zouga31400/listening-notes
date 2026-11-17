// Radio Soirées — Scriptable app and widget (read-only view of the Notion history).
// Reads the two Notion databases with a READ-ONLY Notion connection. Its key is kept in
// this device's keychain (asked once); it never appears in this file.
// Widget: add a Scriptable widget to the home screen and choose this script.

const KEY = "radio_soirees_notion_key";
const HISTORY_DB = "1099479c43a944e0a358727716a0d49b";
const MISSING_DB = "0cfa728c14294956be448e18eb460be1";
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
    case "rich_text": return p.rich_text.map((t) => t.plain_text).join("");
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

async function loadData() {
  let key = await getKey();
  if (!key) return null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const [h, m] = await Promise.all([
        queryAll(key, HISTORY_DB, "Played at", config.runsInWidget ? 1 : 20),
        queryAll(key, MISSING_DB, "Date", 5),
      ]);
      const history = h.map((pg) => {
        const p = pg.properties;
        return {
          id: pg.id, start: prop(p["Start time"]) || prop(p["Played at"]),
          title: prop(p.Title) || "", artist: prop(p.Artist) || "", album: prop(p.Album) || "",
          year: prop(p.Year), decade: prop(p.Decade) || "", genre: prop(p.Genre) || "",
          mood: prop(p.Mood) || "", status: prop(p.Status) || "", link: prop(p.Link) || "",
          shazam: prop(p.Shazam) || "", youtube: prop(p.YouTube) || "",
          cover: prop(p["Cover URL"]) || prop(p.Cover) || "",
        };
      }).filter((r) => r.start && r.status !== "Remove");
      const missing = m.map((pg) => {
        const p = pg.properties;
        return {id: pg.id, date: prop(p.Date) || "", title: prop(p.Title) || "", artist: prop(p.Artist) || "",
                album: prop(p.Album) || "", year: prop(p.Year), shazam: prop(p.Shazam) || "",
                youtube: prop(p.YouTube) || ""};
      });
      const data = {history, missing, at: new Date().toISOString()};
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
    const l = w.addText(label); l.font = Font.boldSystemFont(9); l.textColor = new Color("#e4e6ea");
    const t = w.addText(last.title); t.font = Font.boldSystemFont(13); t.textColor = white; t.lineLimit = 2;
    const a = w.addText(`${last.artist} · ${timeOf(new Date(last.start))}`); a.font = Font.systemFont(10); a.textColor = new Color("#e4e6ea"); a.lineLimit = 1;
    return w;
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
--calme-bg:#dcecfb;--calme:#1d5f99;--groove-bg:#fbefcc;--groove:#7a5800;--ener-bg:#fbdcdc;--ener:#9b2222}
@media (prefers-color-scheme:dark){:root{--bg:#121418;--card:#1e2126;--fg:#f2f3f5;--muted:#9aa0aa;--line:#2b2f36;--accent:#7fb3ff;--seg:#24272d;--segon:#3a3e46;
--calme-bg:#173a52;--calme:#7cc4ff;--groove-bg:#4a3a12;--groove:#ffd166;--ener-bg:#4d1d1d;--ener:#ff8a80}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--fg);font:15px -apple-system,system-ui,sans-serif}
body{padding:calc(env(safe-area-inset-top) + 8px) max(16px,env(safe-area-inset-right)) calc(env(safe-area-inset-bottom) + 24px) max(16px,env(safe-area-inset-left))}
h1{font-size:30px;margin:6px 0 2px;letter-spacing:-.4px}.sub{color:var(--muted);margin:0 0 12px;font-size:14px}
.warn{background:var(--groove-bg);color:var(--groove);border-radius:10px;padding:8px 12px;font-size:13px;margin-bottom:10px}
.seg{display:flex;background:var(--seg);border-radius:10px;padding:2px;margin-bottom:12px;position:sticky;top:env(safe-area-inset-top);z-index:2}
.seg button{flex:1;border:0;background:none;color:var(--fg);font:inherit;font-size:13px;padding:7px 0;border-radius:8px}
.seg button.on{background:var(--segon);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.15)}
select,input{width:100%;font:inherit;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--fg);margin-bottom:10px}
.row{display:flex;gap:12px;align-items:center;padding:8px 4px;border-bottom:1px solid var(--line);text-decoration:none;color:inherit}
.cov{width:56px;height:56px;border-radius:8px;flex:none;background:var(--line);object-fit:cover}
.meta{flex:1;min-width:0}.t{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.a{font-size:13px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.s{font-size:12px;color:var(--muted)}
.right{display:flex;flex-direction:column;align-items:flex-end;gap:5px;flex:none}.time{font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
.chip{font-size:11px;padding:2px 8px;border-radius:99px;font-weight:600}.Calme{background:var(--calme-bg);color:var(--calme)}
.Groove{background:var(--groove-bg);color:var(--groove)}.Énergique{background:var(--ener-bg);color:var(--ener)}
.ex .t{font-style:italic;font-weight:500}.ex{opacity:.75}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}.kpi{background:var(--card);border-radius:14px;padding:12px 14px}
.kpi b{display:block;font-size:28px;font-variant-numeric:tabular-nums}.kpi span{font-size:12px;color:var(--muted)}
.card{background:var(--card);border-radius:14px;padding:14px;margin-bottom:12px}.card h3{margin:0 0 10px;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.cols{display:flex;align-items:flex-end;gap:6px;height:120px;overflow-x:auto}.cols div{flex:1 0 26px;display:flex;flex-direction:column;align-items:center;gap:3px;font-size:10px;color:var(--muted);height:100%;justify-content:flex-end}
.cols i{display:block;width:100%;background:var(--accent);border-radius:4px 4px 0 0;min-height:2px}.cols em{font-style:normal;color:var(--fg);font-size:11px}
.hb{display:grid;grid-template-columns:96px 1fr 30px;gap:8px;align-items:center;font-size:13px;margin:7px 0}.hb .n{text-align:right;font-variant-numeric:tabular-nums}
.hb .bar{height:10px;border-radius:5px;background:var(--line);overflow:hidden}.hb .bar i{display:block;height:100%;border-radius:5px;background:var(--accent)}
.hb .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.empty{color:var(--muted);padding:24px 4px;text-align:center}
</style></head><body>
<h1>Radio Soirées</h1><p class="sub" id="sub"></p><div id="warn"></div>
<div class="seg"><button data-tab="soiree" class="on">Soirée</button><button data-tab="stats">Statistiques</button><button data-tab="absents">Absents</button><button data-tab="cherche">Recherche</button></div>
<main id="main"></main>
<script>
const D=${json};
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e};
const pp=(d)=>{const o={};for(const p of new Intl.DateTimeFormat('fr-FR',{timeZone:'Europe/Paris',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(d))o[p.type]=p.value;return o};
const evOf=(d)=>{const p=pp(new Date(d.getTime()-216e5));return p.year+'-'+p.month+'-'+p.day};
const fmtEv=(s,o)=>new Intl.DateTimeFormat('fr-FR',Object.assign({timeZone:'UTC'},o||{weekday:'long',day:'numeric',month:'long'})).format(new Date(s+'T12:00:00Z'));
const H=D.history.map(r=>{const d=new Date(r.start);const p=pp(d);return Object.assign({},r,{d,ev:evOf(d),time:p.hour+'h'+p.minute})}).sort((a,b)=>a.d-b.d);
const T=H.filter(r=>r.status!=='Excerpt');
const evs=[...new Set(H.map(r=>r.ev))].sort().reverse();
let cur=evs[0],tab='soiree';
const nT=(ev)=>T.filter(r=>r.ev===ev).length;
document.getElementById('sub').textContent=evs.length?('Dernière soirée : '+fmtEv(evs[0])+', '+nT(evs[0])+' titres'):'Aucune donnée';
if(D.offline){const w=el('div','warn',D.offline);document.getElementById('warn').append(w)}
function row(r,withDate){
  const a=el('a','row'+(r.status==='Excerpt'?' ex':''));a.href=r.link||r.shazam||r.youtube||'#';
  const img=el('img','cov');img.loading='lazy';img.alt='';if(r.cover)img.src=r.cover;a.append(img);
  const m=el('div','meta');m.append(el('div','t',r.title),el('div','a',r.artist),el('div','s',[r.status==='Excerpt'?'Extrait court':'',r.album,r.year].filter(Boolean).join(' · ')));a.append(m);
  const rt=el('div','right');rt.append(el('span','time',withDate?fmtEv(r.ev,{day:'2-digit',month:'2-digit'})+' '+r.time:r.time));if(r.mood)rt.append(el('span','chip '+r.mood,r.mood));a.append(rt);return a}
function bars(title,items,color){const c=el('div','card');c.append(el('h3',null,title));const mx=Math.max(1,...items.map(x=>x[1]));
  for(const [k,v] of items){const b=el('div','hb');const bar=el('div','bar');const i=el('i');i.style.width=(100*v/mx)+'%';if(color)i.style.background=color(k);bar.append(i);b.append(el('span','name',k),bar,el('span','n',String(v)));c.append(b)}return c}
const count=(arr,f)=>{const m=new Map();for(const r of arr){const k=f(r);if(k)m.set(k,(m.get(k)||0)+1)}return [...m]};
function render(){const M=document.getElementById('main');M.replaceChildren();
  if(tab==='soiree'){if(!evs.length){M.append(el('div','empty','Aucun titre pour l\\'instant.'));return}
    const s=el('select');for(const e of evs){const o=el('option',null,fmtEv(e)+' · '+nT(e)+' titres');o.value=e;s.append(o)}s.value=cur;s.onchange=()=>{cur=s.value;render()};M.append(s);
    for(const r of H.filter(r=>r.ev===cur))M.append(row(r,false))}
  if(tab==='stats'){const k=el('div','kpis');const kpi=(n,l)=>{const d=el('div','kpi');d.append(el('b',null,String(n)),el('span',null,l));k.append(d)};
    kpi(T.length,'titres retenus');kpi(evs.length,'soirées');kpi(new Set(T.map(r=>r.artist.toLowerCase())).size,'artistes');kpi(D.missing.length,'absents du service');M.append(k);
    const c=el('div','card');c.append(el('h3',null,'Titres par soirée'));const cols=el('div','cols');const last=evs.slice(0,14).reverse();const mx=Math.max(1,...last.map(nT));
    for(const e of last){const d=el('div');const i=el('i');i.style.height=(85*nT(e)/mx)+'%';d.append(el('em',null,String(nT(e))),i,el('span',null,fmtEv(e,{day:'2-digit',month:'2-digit'})));d.onclick=()=>{cur=e;tab='soiree';setTab()};cols.append(d)}c.append(cols);M.append(c);
    const mc={Calme:'var(--calme)',Groove:'var(--groove)','Énergique':'var(--ener)'};
    M.append(bars('Ambiances',['Calme','Groove','Énergique'].map(x=>[x,T.filter(r=>r.mood===x).length]),k=>mc[k]));
    M.append(bars('Décennies',count(T,r=>r.decade).sort((a,b)=>parseInt(a[0])-parseInt(b[0]))));
    M.append(bars('Artistes les plus diffusés',count(T,r=>r.artist).sort((a,b)=>b[1]-a[1]).slice(0,8)));
    M.append(bars('Genres',count(T,r=>r.genre).sort((a,b)=>b[1]-a[1]).slice(0,8)))}
  if(tab==='absents'){if(!D.missing.length){M.append(el('div','empty','Aucun titre absent.'));return}
    for(const r of D.missing){const a=el('a','row');a.href=r.youtube||r.shazam||'#';const m=el('div','meta');m.append(el('div','t',r.title),el('div','a',r.artist),el('div','s',[r.album,r.year].filter(Boolean).join(' · ')));a.append(m);
      const rt=el('div','right');rt.append(el('span','time',r.date?fmtEv(r.date.slice(0,10),{day:'2-digit',month:'2-digit'}):''));a.append(rt);M.append(a)}}
  if(tab==='cherche'){const i=el('input');i.type='search';i.placeholder='Artiste, titre, album…';const list=el('div');M.append(i,list);
    const go=()=>{const q=i.value.trim().toLowerCase();list.replaceChildren();if(q.length<2){list.append(el('div','empty','Tape au moins 2 lettres.'));return}
      const res=H.filter(r=>(r.title+' '+r.artist+' '+r.album).toLowerCase().includes(q)).reverse();if(!res.length)list.append(el('div','empty','Aucun résultat.'));for(const r of res.slice(0,100))list.append(row(r,true))};
    i.oninput=go;go();setTimeout(()=>i.focus(),50)}}
function setTab(){document.querySelectorAll('.seg button').forEach(b=>b.classList.toggle('on',b.dataset.tab===tab));render();window.scrollTo(0,0)}
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;setTab()});
render();
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
