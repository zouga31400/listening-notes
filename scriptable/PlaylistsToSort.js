// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: green; icon-glyph: music;
//
// Playlists to sort: a dashboard for the "Playlists to sort" Notion database
// (workflow "Sort a playlist by mood and period" of zouga31400/listening-notes).
//
// In the app: list of the playlists with their schedule, last sort and result; tap a
// playlist to sort it now, change its day / hour / frequency or switch it on and off;
// "+" adds a playlist; the genre rules and the sorting settings can be edited too.
// As a widget (medium or large): the next sorts. From a Shortcut or a link:
//   scriptable:///run/PlaylistsToSort?sort=<playlist name>   sorts that playlist now.
//
// First run: the script asks for two secrets and keeps them in the iOS Keychain (they are
// never written in this file):
//   - the Notion integration token (the one that has the "Musique" page connected);
//   - optional, to start a sort at once: a GitHub fine-grained token for the repository,
//     permission "Actions: Read and write". Without it, "Sort now" only clears
//     "Last sorted" and the sort starts at the next hourly check.

// ---- settings (no secret here) ----
const REPO = "zouga31400/listening-notes";
const WORKFLOW = "sort-playlist.yml";
const SORT_DB = "cce7a625036646a791f987eb35a319ef";      // Playlists to sort
const GENRE_DB = "41b481d1393d81fca043ee542b604df6";     // Règles de genre
const SETTINGS_DB = "41b481d1393d81359babf1ca3441823c";  // Réglages du tri
const NOTION_VERSION = "2022-06-28";
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const FREQUENCIES = ["Daily", "Weekly", "Monthly"];
const DEFAULT_DAY = 4, DEFAULT_HOUR = 19;                // Friday 19:00

// ---- pure functions: schedule in Paris time (same rules as sort_playlist.py) ----
// PURE-START
const TZ = "Europe/Paris";
const PARIS_FORMAT = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false
});

function parisParts(date) {
  const p = {};
  for (const x of PARIS_FORMAT.formatToParts(date)) p[x.type] = x.value;
  return { y: +p.year, m: +p.month, d: +p.day, h: +p.hour % 24, min: +p.minute };
}

function parisOffset(ms) {
  const p = parisParts(new Date(ms));
  return Date.UTC(p.y, p.m - 1, p.d, p.h, p.min) - Math.floor(ms / 60000) * 60000;
}

function parisToDate(y, m, d, h) {
  const wall = Date.UTC(y, m - 1, d, h);
  const first = wall - parisOffset(wall);
  return new Date(wall - parisOffset(first));
}

function weekdayIndex(y, m, d) {            // Monday = 0
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
}

function slotMatches(frequency, day, y, m, d) {
  if (frequency === "Daily") return true;
  if (weekdayIndex(y, m, d) !== day) return false;
  return frequency !== "Monthly" || d <= 7;  // monthly = the first such day of the month
}

function scheduleOf(row) {
  const freq = FREQUENCIES.indexOf(row.frequency) >= 0 ? row.frequency : "Weekly";
  const day = DAYS.indexOf(row.day) >= 0 ? DAYS.indexOf(row.day) : DEFAULT_DAY;
  const hour = Number.isInteger(row.hour) && row.hour >= 0 && row.hour <= 23 ? row.hour : DEFAULT_HOUR;
  return { freq, day, hour };
}

// offset 0 = today in Paris; slot at or before `now` (back) or after it (ahead)
function findSlot(row, now, direction) {
  const s = scheduleOf(row);
  const today = parisParts(now);
  for (let i = 0; i <= 40; i++) {
    const k = direction < 0 ? -i : i;
    const base = new Date(Date.UTC(today.y, today.m - 1, today.d + k));
    const y = base.getUTCFullYear(), m = base.getUTCMonth() + 1, d = base.getUTCDate();
    if (!slotMatches(s.freq, s.day, y, m, d)) continue;
    const slot = parisToDate(y, m, d, s.hour);
    if (direction < 0 ? slot <= now : slot > now) return slot;
  }
  return null;
}

const lastSlot = (row, now) => findSlot(row, now, -1);
const nextSlot = (row, now) => findSlot(row, now, 1);

function parseWhen(value) {
  if (!value) return null;
  const only = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (only) return parisToDate(+only[1], +only[2], +only[3], 0);
  const date = new Date(value);
  return isNaN(date) ? null : date;
}

function isDue(row, now) {
  const slot = lastSlot(row, now);
  const last = parseWhen(row.lastSorted);
  return !!slot && (!last || last < slot);
}

function shortDate(date) {
  if (!date) return "–";
  const p = parisParts(date), two = n => String(n).padStart(2, "0");
  return `${two(p.d)}/${two(p.m)} ${two(p.h)}:${two(p.min)}`;
}

function scheduleText(row) {
  const s = scheduleOf(row);
  const hour = String(s.hour).padStart(2, "0") + ":00";
  return s.freq === "Daily" ? `Daily ${hour}` : `${s.freq} ${DAYS[s.day]} ${hour}`;
}
// PURE-END

// ---- Keychain ----
async function secret(key, title, message, optional) {
  if (Keychain.contains(key)) return Keychain.get(key);
  const a = new Alert();
  a.title = title;
  a.message = message;
  a.addSecureTextField("token");
  a.addAction("Save");
  a.addCancelAction(optional ? "Skip" : "Cancel");
  const choice = await a.presentAlert();
  const value = choice === 0 ? a.textFieldValue(0).trim() : "";
  if (value) Keychain.set(key, value);
  return value;
}

async function notionToken() {
  const token = await secret("notion_token", "Notion token",
    "Token of the Notion integration connected to the Musique page.", false);
  if (!token) throw new Error("Notion token missing");
  return token;
}

const githubToken = () => secret("github_token", "GitHub token (optional)",
  "Fine-grained token for " + REPO + ", permission Actions: Read and write. Skip to sort at the next hourly check instead.", true);

// ---- Notion ----
async function notion(method, path, body) {
  const req = new Request("https://api.notion.com/v1" + path);
  req.method = method;
  req.headers = {
    "Authorization": "Bearer " + await notionToken(),
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json"
  };
  if (body) req.body = JSON.stringify(body);
  const res = await req.loadJSON();
  if (res.object === "error") throw new Error(res.message || res.code);
  return res;
}

async function queryAll(db, body) {
  const pages = [];
  let cursor = null;
  do {
    const res = await notion("POST", `/databases/${db}/query`,
      Object.assign({ page_size: 100 }, body || {}, cursor ? { start_cursor: cursor } : {}));
    pages.push(...res.results);
    cursor = res.has_more ? res.next_cursor : null;
  } while (cursor);
  return pages;
}

const text = rich => (rich || []).map(t => t.plain_text).join("");
const titleOf = props => {
  for (const k in props) if (props[k].type === "title") return text(props[k].title);
  return "";
};
const richText = value => ({ rich_text: value ? [{ text: { content: value.slice(0, 2000) } }] : [] });

function toPlaylist(page) {
  const p = page.properties;
  const select = n => (p[n] && p[n].select && p[n].select.name) || "";
  return {
    id: page.id, name: titleOf(p).trim(),
    active: !!(p.Active && p.Active.checkbox),
    frequency: select("Frequency"), day: select("Day"),
    hour: p.Hour && typeof p.Hour.number === "number" ? p.Hour.number : null,
    lastSorted: (p["Last sorted"] && p["Last sorted"].date && p["Last sorted"].date.start) || "",
    copies: p.Copies ? p.Copies.number : null,
    result: text(p.Result && p.Result.rich_text)
  };
}

const loadPlaylists = async () => (await queryAll(SORT_DB)).map(toPlaylist).filter(r => r.name);
const update = (id, properties) => notion("PATCH", `/pages/${id}`, { properties });

// ---- GitHub ----
async function dispatchSort(name) {
  const token = await githubToken();
  if (!token) return false;
  const req = new Request(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`);
  req.method = "POST";
  req.headers = {
    "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"
  };
  req.body = JSON.stringify({ ref: "main", inputs: { playlists: name } });
  await req.load();
  const status = req.response ? req.response.statusCode : 0;
  if (status !== 204) throw new Error("GitHub answered " + status + " (check the token)");
  return true;
}

async function sortNow(row) {
  await update(row.id, { "Active": { checkbox: true }, "Last sorted": { date: null } });
  const started = await dispatchSort(row.name);
  return started ? "Sort started on GitHub." : "Last sorted cleared: the sort starts at the next hourly check.";
}

// ---- small dialogs ----
async function info(title, message) {
  const a = new Alert();
  a.title = title;
  if (message) a.message = message;
  a.addAction("OK");
  await a.presentAlert();
}

async function choose(title, options, message) {
  const a = new Alert();
  a.title = title;
  if (message) a.message = message;
  options.forEach(o => a.addAction(o));
  a.addCancelAction("Cancel");
  const i = await a.presentSheet();
  return i < 0 ? null : i;
}

async function ask(title, fields, message) {   // fields: [{label, value}] -> values or null
  const a = new Alert();
  a.title = title;
  if (message) a.message = message;
  fields.forEach(f => a.addTextField(f.label, f.value || ""));
  a.addAction("Save");
  a.addCancelAction("Cancel");
  if (await a.presentAlert() !== 0) return null;
  return fields.map((f, i) => a.textFieldValue(i).trim());
}

async function guarded(fn) {
  try { await fn(); } catch (e) { await info("Error", String(e.message || e)); }
}

// ---- screens ----
function line(row, title, subtitle, onSelect) {
  const r = new UITableRow();
  r.height = subtitle ? 62 : 48;
  r.addText(title, subtitle).widthWeight = 100;
  if (onSelect) { r.dismissOnSelect = false; r.onSelect = onSelect; }
  return r;
}

async function changeSchedule(row) {
  const f = await choose("Frequency", FREQUENCIES, row.name);
  if (f === null) return;
  const props = { "Frequency": { select: { name: FREQUENCIES[f] } } };
  if (FREQUENCIES[f] !== "Daily") {
    const d = await choose(FREQUENCIES[f] === "Monthly" ? "Day (first of the month)" : "Day", DAYS, row.name);
    if (d === null) return;
    props["Day"] = { select: { name: DAYS[d] } };
  }
  const h = await ask("Hour (Paris time, 0-23)", [{ label: "19", value: String(scheduleOf(row).hour) }], row.name);
  if (h === null) return;
  const hour = parseInt(h[0], 10);
  if (!(hour >= 0 && hour <= 23)) return info("Hour", "Enter a whole number from 0 to 23.");
  props["Hour"] = { number: hour };
  await update(row.id, props);
}

async function playlistMenu(row) {
  const now = new Date();
  const due = row.active && isDue(row, now);
  const summary = [
    scheduleText(row) + (row.active ? "" : " (off)"),
    "Last sorted: " + shortDate(parseWhen(row.lastSorted)),
    "Next: " + (row.active ? (due ? "at the next hourly check" : shortDate(nextSlot(row, now))) : "–"),
    row.result ? "Result: " + row.result : ""
  ].filter(Boolean).join("\n");
  const i = await choose(row.name, ["Sort now", "Change the schedule", row.active ? "Switch off" : "Switch on"], summary);
  if (i === 0) await info(row.name, await sortNow(row));
  else if (i === 1) await changeSchedule(row);
  else if (i === 2) await update(row.id, { "Active": { checkbox: !row.active } });
}

async function addPlaylist() {
  const v = await ask("Add a playlist", [{ label: "Exact playlist name", value: "" }],
    "Sorted every Friday at 19:00 until you change its schedule.");
  if (!v || !v[0]) return;
  const existing = (await loadPlaylists()).find(r => r.name.toLowerCase() === v[0].toLowerCase());
  if (existing) {
    await update(existing.id, { "Active": { checkbox: true } });
    return info(existing.name, "Already in the list: switched on.");
  }
  await notion("POST", "/pages", {
    parent: { database_id: SORT_DB },
    properties: { "Name": { title: [{ text: { content: v[0] } }] }, "Active": { checkbox: true } }
  });
  await info(v[0], "Added. It is sorted within the hour.");
}

async function genreScreen() {
  const table = new UITable();
  const render = async () => {
    table.removeAllRows();
    const header = line(null, "Genre rules", "A playlist = kept apart from the mood copies. Points are added to the energy.");
    table.addRow(header);
    const add = line(null, "+ Add a genre", "", async () => {
      await guarded(async () => {
        const v = await ask("Add a genre", [{ label: "Genre as Qobuz names it", value: "" }]);
        if (!v || !v[0]) return;
        await notion("POST", "/pages", {
          parent: { database_id: GENRE_DB },
          properties: { "Genre": { title: [{ text: { content: v[0] } }] }, "Energy points": { number: 0 } }
        });
        await render();
      });
    });
    table.addRow(add);
    const pages = await queryAll(GENRE_DB);
    const genres = pages.map(p => ({
      id: p.id, genre: titleOf(p.properties).trim(),
      playlist: text(p.properties.Playlist && p.properties.Playlist.rich_text),
      points: (p.properties["Energy points"] && p.properties["Energy points"].number) || 0
    })).filter(g => g.genre).sort((a, b) => a.genre.localeCompare(b.genre));
    for (const g of genres) {
      const sub = [g.playlist ? "→ " + g.playlist : "", g.points ? (g.points > 0 ? "+" : "") + g.points + " pts" : ""]
        .filter(Boolean).join("  ·  ") || "no rule";
      table.addRow(line(null, g.genre, sub, async () => {
        await guarded(async () => {
          const v = await ask(g.genre, [{ label: "Playlist (empty = none)", value: g.playlist },
            { label: "Energy points", value: String(g.points) }]);
          if (!v) return;
          const points = parseInt(v[1] || "0", 10);
          if (isNaN(points)) return info("Energy points", "Enter a whole number (negative allowed).");
          await update(g.id, { "Playlist": richText(v[0]), "Energy points": { number: points } });
          await render();
        });
      }));
    }
    table.reload();
  };
  await render();
  await table.present(false);
}

async function settingsScreen() {
  const table = new UITable();
  const render = async () => {
    table.removeAllRows();
    table.addRow(line(null, "Sorting settings", "Calm up to / Energetic from: energy thresholds (0-100)."));
    const pages = await queryAll(SETTINGS_DB);
    for (const p of pages) {
      const name = titleOf(p.properties).trim();
      const value = text(p.properties.Value && p.properties.Value.rich_text);
      const help = text(p.properties.Help && p.properties.Help.rich_text);
      table.addRow(line(null, name, value, async () => {
        await guarded(async () => {
          const v = await ask(name, [{ label: "Value", value }], help);
          if (!v) return;
          if (/^(calm up to|energetic from)$/i.test(name) && !/^\d+$/.test(v[0]))
            return info(name, "Enter a whole number.");
          await update(p.id, { "Value": richText(v[0]) });
          await render();
        });
      }));
    }
    table.reload();
  };
  await render();
  await table.present(false);
}

async function mainScreen() {
  const table = new UITable();
  table.showSeparators = true;
  const render = async () => {
    table.removeAllRows();
    const top = [
      ["+ Add a playlist", async () => addPlaylist()],
      ["Genre rules", async () => genreScreen()],
      ["Sorting settings", async () => settingsScreen()],
      ["Forget saved tokens", async () => {
        const i = await choose("Forget the Notion and GitHub tokens saved on this iPhone?", ["Forget"]);
        if (i === 0) { Keychain.remove("notion_token"); Keychain.remove("github_token"); }
      }]
    ];
    for (const [title, action] of top) {
      table.addRow(line(null, title, "", async () => { await guarded(action); await guarded(render); }));
    }
    const now = new Date();
    const rows = await loadPlaylists();
    rows.sort((a, b) => (b.active - a.active) || a.name.localeCompare(b.name));
    for (const r of rows) {
      const due = r.active && isDue(r, now);
      const status = !r.active ? "⏸" : due ? "⏳" : "✅";
      const sub = scheduleText(r) + " · last " + shortDate(parseWhen(r.lastSorted)) +
        (r.active && !due ? " · next " + shortDate(nextSlot(r, now)) : due ? " · due" : "") +
        (r.copies != null ? " · " + r.copies + " copies" : "");
      table.addRow(line(null, `${status} ${r.name}`, sub, async () => {
        await guarded(() => playlistMenu(r));
        await guarded(render);
      }));
    }
    table.reload();
  };
  await render();
  await table.present(false);
}

// ---- widget ----
async function buildWidget() {
  const w = new ListWidget();
  w.backgroundColor = new Color("#1c1c1e");
  const title = w.addText("Playlists to sort");
  title.font = Font.boldSystemFont(13);
  title.textColor = Color.white();
  w.addSpacer(6);
  try {
    if (!Keychain.contains("notion_token")) throw new Error("Open the script once to set it up");
    const now = new Date();
    const rows = (await loadPlaylists()).filter(r => r.active);
    const items = rows.map(r => ({ r, due: isDue(r, now), next: nextSlot(r, now) }))
      .sort((a, b) => (b.due - a.due) || ((a.next || 0) - (b.next || 0)));
    const max = config.widgetFamily === "large" ? 9 : config.widgetFamily === "small" ? 3 : 5;
    for (const it of items.slice(0, max)) {
      const t = w.addText(it.r.name);
      t.font = Font.mediumSystemFont(12);
      t.textColor = Color.white();
      t.lineLimit = 1;
      const s = w.addText(it.due ? "due: next hourly check" : "next " + shortDate(it.next) + " · last " + shortDate(parseWhen(it.r.lastSorted)));
      s.font = Font.systemFont(10);
      s.textColor = it.due ? Color.orange() : Color.gray();
      w.addSpacer(3);
    }
    if (!items.length) w.addText("No active playlist").textColor = Color.gray();
  } catch (e) {
    const t = w.addText(String(e.message || e));
    t.font = Font.systemFont(11);
    t.textColor = Color.red();
  }
  w.refreshAfterDate = new Date(Date.now() + 30 * 60 * 1000);
  return w;
}

// ---- start ----
async function run() {
  if (config.runsInWidget) {
    Script.setWidget(await buildWidget());
  } else if (args.queryParameters && args.queryParameters.sort) {
    const name = args.queryParameters.sort.trim().toLowerCase();
    await guarded(async () => {
      const row = (await loadPlaylists()).find(r => r.name.toLowerCase() === name);
      if (!row) return info("Sort", "Playlist not found in the Notion list: " + args.queryParameters.sort);
      await info(row.name, await sortNow(row));
    });
  } else {
    await guarded(mainScreen);
  }
}

if (typeof Script !== "undefined") {
  await run();
  Script.complete();
}
