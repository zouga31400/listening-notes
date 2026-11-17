"""Copies of a playlist sorted by mood (tempo and energy) and period.

For each chosen playlist, every
track gets the same classification as the radio tracks (radio_listen.py):
  - its original year (oldest release among the relevant versions), giving the period
    60-80s / 90-2000s / 2010s+ and the decade;
  - its tempo (BPM) and energy measured on Deezer's 30-second preview, giving the
    mood Calme / Groove / Energique (thresholds in config.txt).
The track then goes to the copies named by sort_playlist_name, e.g. "Moto Groove 90-2000s".
The original playlist is never modified.

Which playlists: the workflow input when given; otherwise the ticked ("Active") rows of
the "Playlists to sort" Notion database (notion_sort_db in config.txt, NOTION_TOKEN
secret), whose "Last sorted", "Copies" and "Result" are then filled in; config.txt's
sort_playlists is only used when no Notion database is set.

Schedule, per Notion row: "Frequency" (Daily / Weekly / Monthly), "Day" (weekly: that
day; monthly: the first such day of the month) and "Hour" (Paris time); empty means
every Friday at 19:00. The workflow checks every hour (--due mode, without the audio
tools); a scheduled run sorts only the rows whose last scheduled time came after their
"Last sorted", so a missed time is caught up at the next check. A run by hand with an
empty input sorts every ticked row.

Sorting rules, from two Notion databases (notion_sort_settings_db, notion_genre_db):
  - "Sorting settings": the calm / energetic energy thresholds and the copy name;
  - "Genre rules": one row per genre of the service (the album's genre). A row with a
    "Playlist" sends the tracks of that genre to that playlist only (no copy by mood and
    period; no audio analysis); "Energy points" are added to the measured energy before
    the mood is chosen (Metal +20, Rock +10...). Genres met but not in the table are added
    to it (points 0, no playlist) so they only have to be filled in.
config.txt keeps the default values, used when a database is not set or can't be read.

Copies created by this script (description "Created by sort_playlist.py") are kept in
sync with the original: each run adds what is missing and removes tracks that left the
original or changed category. A playlist with the same name not created by the script
is only completed, never trimmed.
"""
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from analyze_playlists import MusicService, flat, load_config, log
from radio_listen import (decade_label, log_detail, mood_of, norm, period_label, pick,
                          preview_features, search_title)

MARKER = "Created by sort_playlist.py"
PARIS = ZoneInfo("Europe/Paris")
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DEFAULT_DAY, DEFAULT_HOUR = 4, 19   # Friday, 19:00
BATCH = 50


def entries(service, playlist_id):
    """(track id, playlist_track_id) of each entry of a playlist."""
    found, offset = [], 0
    while True:
        data = service.call("GET", "/playlist/get", playlist_id=playlist_id,
                            extra="tracks", limit=500, offset=offset)
        items = (data.get("tracks") or {}).get("items", [])
        found.extend((t["id"], t.get("playlist_track_id")) for t in items if t.get("id"))
        if len(items) < 500:
            return found
        offset += 500


def remove(service, playlist_id, playlist_track_ids):
    for i in range(0, len(playlist_track_ids), BATCH):
        service.call("POST", "/playlist/deleteTracks", playlist_id=playlist_id,
                     playlist_track_ids=",".join(str(t) for t in playlist_track_ids[i:i + BATCH]))


def original_info(service, track):
    """(year, genre): oldest release year among the relevant versions of the track (a
    remaster counts in its original decade, as in radio_listen.classify_existing) and the
    genre of the album picked."""
    artist = (track.get("performer") or {}).get("name") or ""
    title = track.get("title") or ""
    try:
        versions = service.call("GET", "/catalog/search", query=f"{artist} {search_title(title)}",
                                limit=30)
        versions = (versions.get("tracks") or {}).get("items", [])
    except Exception:
        versions = []
    best = pick(versions + [track], norm(artist), norm(title))
    year = (best or {}).get("_original_year")
    if not year:
        date = ((track.get("album") or {}).get("release_date_original") or "")[:4]
        year = int(date) if date.isdigit() else None
    genre = ""
    for candidate in (best, track):
        genre = (((candidate or {}).get("album") or {}).get("genre") or {}).get("name") or ""
        if genre:
            break
    return year, genre


def original_year(service, track):
    return original_info(service, track)[0]


def genre_matches(rule, genre):
    """True when the rule's genre is the track's genre or one of its words, ignoring case
    and accents: "Rock" matches "Pop/Rock", "Metal" matches "Heavy Metal"."""
    r, g = flat(rule), flat(genre)
    return bool(r and g) and re.search(rf"(?<![a-z0-9]){re.escape(r)}(?![a-z0-9])", g) is not None


def genre_effect(rules, genre):
    """(dedicated playlist or "", energy points) for a genre: the first matching row with a
    playlist wins; the points of every matching row add up."""
    playlist, points = "", 0
    for rule in rules:
        if genre_matches(rule["genre"], genre):
            playlist = playlist or rule["playlist"]
            points += rule["points"]
    return playlist, points


def rules_from_rows(rows):
    """Genre rules from the pages of the "Genre rules" database."""
    rules = []
    for page in rows:
        props = page.get("properties", {})
        title = next((v.get("title") for v in props.values() if v.get("type") == "title"), [])
        genre = "".join(t.get("plain_text", "") for t in title or []).strip()
        text = "".join(t.get("plain_text", "")
                       for t in (props.get("Playlist") or {}).get("rich_text") or []).strip()
        points = (props.get("Energy points") or {}).get("number")
        if genre:
            rules.append({"genre": genre, "playlist": text,
                          "points": int(points) if isinstance(points, (int, float)) else 0})
    return rules


def settings_from_rows(rows):
    """Overrides of config.txt keys from the pages of the "Sorting settings" database
    (Setting = title, Value = text)."""
    keys = {"calm up to": "calm_max_energy", "energetic from": "energetic_min_energy",
            "copy name": "sort_playlist_name"}
    found = {}
    for page in rows:
        props = page.get("properties", {})
        title = next((v.get("title") for v in props.values() if v.get("type") == "title"), [])
        name = flat("".join(t.get("plain_text", "") for t in title or []))
        value = "".join(t.get("plain_text", "")
                        for t in (props.get("Value") or {}).get("rich_text") or []).strip()
        number = (props.get("Value") or {}).get("number")
        if value == "" and isinstance(number, (int, float)):
            value = str(int(number))
        if name in keys and value:
            if keys[name] != "sort_playlist_name" and not value.lstrip("-").isdigit():
                continue
            found[keys[name]] = value
    return found


def load_rules(config):
    """(config with the Notion settings applied, genre rules, notion, genre database id).
    The config.txt values are kept when a database is not set or can't be read."""
    from notion_lists import Notion, database_id
    token = os.environ.get("NOTION_TOKEN", "").strip()
    settings_db = (config.get("notion_sort_settings_db") or "").strip()
    genre_db = (config.get("notion_genre_db") or "").strip()
    if not token or not (settings_db or genre_db):
        return config, [], None, None
    notion = Notion(token)
    config, rules, genre_id = dict(config), [], None
    if settings_db:
        try:
            rows = list(notion.paginate("POST", f"/databases/{database_id(settings_db)}/query", {}))
            found = settings_from_rows(rows)
            config.update(found)
            log(f"{len(found)} sorting setting(s) read from Notion")
        except Exception as e:
            log(f"Sorting settings not read ({type(e).__name__}); config.txt values used")
    if genre_db:
        try:
            genre_id = database_id(genre_db)
            rows = list(notion.paginate("POST", f"/databases/{genre_id}/query", {}))
            rules = rules_from_rows(rows)
            log(f"{len(rules)} genre rule(s) read from Notion")
        except Exception as e:
            genre_id = None
            log(f"Genre rules not read ({type(e).__name__}); no genre rule applied")
    return config, rules, notion, genre_id


def add_new_genres(notion, genre_id, rules, genres):
    """Adds the genres met and not matched by any rule to the "Genre rules" database."""
    if not notion or not genre_id:
        return
    for genre in sorted(genres):
        if not genre or any(genre_matches(r["genre"], genre) for r in rules):
            continue
        try:
            notion.call("POST", "/pages", {
                "parent": {"database_id": genre_id},
                "properties": {"Genre": {"title": [{"text": {"content": genre}}]},
                               "Energy points": {"number": 0}}})
            rules.append({"genre": genre, "playlist": "", "points": 0})
            log(f"   New genre added to the Genre rules table: {genre}")
        except Exception as e:
            log(f"   Could not add a genre to Notion ({type(e).__name__})")
            return


def copy_name(pattern, source, year, mood):
    values = {"{playlist}": source.strip(), "{mood}": mood or "", "{period}": period_label(year),
              "{decade}": decade_label(year), "{year}": str(year or "")}
    used = [k for k in values if k in pattern]
    if any(not values[k] for k in used):
        return None
    for k in used:
        pattern = pattern.replace(k, values[k])
    return " ".join(pattern.split())


def sort_one(service, source, playlists, config, pattern, rules=(), genres_seen=None):
    tracks = service.playlist_tracks(source["id"])
    log(f"Sorting '{source['name']}': {len(tracks)} tracks")
    groups, unsorted, no_audio, separate = {}, 0, 0, set()
    for n, t in enumerate(tracks, 1):
        artist = (t.get("performer") or {}).get("name") or ""
        title = t.get("title") or ""
        year, genre = original_info(service, t)
        if genres_seen is not None and genre:
            genres_seen[genre] = genres_seen.get(genre, 0) + 1
        dedicated, points = genre_effect(rules, genre)
        if dedicated:
            # A genre kept apart: its own playlist, no analysis, no copy by mood and period.
            groups.setdefault(dedicated, []).append(t["id"])
            separate.add(dedicated)
            log_detail(f"   {artist} - {title}: genre {genre} -> {dedicated}")
            time.sleep(0.2)
            continue
        feats = preview_features((t.get("isrc") or "").strip().upper() or None, artist, title)
        if feats and points:
            feats = dict(feats, energy=feats["energy"] + points)
        mood = mood_of(feats, config) if feats else ""
        if not feats:
            no_audio += 1
        name = copy_name(pattern, source["name"], year, mood)
        if name is None:
            unsorted += 1
            log_detail(f"   ? {artist} - {title}: year {year or '?'}, no category")
        else:
            groups.setdefault(name, []).append(t["id"])
            log_detail(f"   {artist} - {title}: {year}, {genre}, {feats['bpm']} BPM, "
                       f"energy {feats['energy']} -> {name}")
        if n % 25 == 0:
            log(f"   {n}/{len(tracks)} tracks analyzed")
        time.sleep(0.2)

    by_name = {flat(p.get("name")): p for p in playlists}
    added = removed = created = 0
    for name in sorted(groups):
        wanted = list(dict.fromkeys(groups[name]))
        target = by_name.get(flat(name))
        if target is None:
            playlist_id = service.create_playlist(
                name, f"{MARKER} (genre playlist)" if name in separate
                else f"{MARKER} from the playlist {source['name']}")
            created += 1
            current = []
            managed = True
        else:
            playlist_id = target["id"]
            current = entries(service, playlist_id)
            managed = MARKER.lower() in (target.get("description") or "").lower()
        present = {tid for tid, _ in current}
        missing = [tid for tid in wanted if tid not in present]
        for i in range(0, len(missing), BATCH):
            service.call("POST", "/playlist/addTracks", playlist_id=playlist_id,
                         track_ids=",".join(str(x) for x in missing[i:i + BATCH]),
                         no_duplicate="true")
        added += len(missing)
        extra = []
        if managed and name not in separate:
            keep, seen = set(wanted), set()
            for tid, entry in current:
                if (tid not in keep or tid in seen) and entry:
                    extra.append(entry)
                seen.add(tid)
            if extra:
                remove(service, playlist_id, extra)
            removed += len(extra)
        log(f"   {name}: {len(wanted)} tracks"
            + (f", +{len(missing)}" if missing else "") + (f", -{len(extra)}" if extra else "")
            + ("" if managed else " (not created by this script: only completed)")
            + (" (genre playlist: only completed)" if name in separate else ""))

    # Copies of this playlist that no longer get any track are emptied.
    prefix = MARKER.lower() + " from the playlist " + source["name"].strip().lower()
    for p in playlists:
        if (p.get("description") or "").strip().lower() == prefix and flat(p.get("name")) \
                not in {flat(g) for g in groups}:
            old = [e for _, e in entries(service, p["id"]) if e]
            if old:
                remove(service, p["id"], old)
                removed += len(old)
                log(f"   {p['name']}: emptied (no track left in this category)")
    log(f"   Done: {len(groups)} copies, {created} created, {added} additions, {removed} removals, "
        f"{unsorted} tracks without year or mood ({no_audio} without audio analysis)")
    return {name: len(dict.fromkeys(ids)) for name, ids in groups.items()}, unsorted


def last_slot(frequency, day, hour, now):
    """Latest scheduled time at or before now (Paris time)."""
    frequency = (frequency or "weekly").strip().lower()
    for back in range(0, 40):
        d = (now - timedelta(days=back)).replace(hour=hour, minute=0, second=0, microsecond=0)
        if d > now:
            continue
        if frequency == "daily":
            return d
        if d.weekday() != day:
            continue
        if frequency == "monthly" and d.day > 7:
            continue
        return d
    return None


def parse_when(value):
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.replace(tzinfo=PARIS) if when.tzinfo is None else when


def row_schedule(props):
    """(frequency, weekday 0-6, hour) of a Notion row, with the defaults."""
    def select(name):
        return ((props.get(name) or {}).get("select") or {}).get("name") or ""
    day = select("Day").strip().lower()
    day = DAYS.index(day) if day in DAYS else DEFAULT_DAY
    hour = (props.get("Hour") or {}).get("number")
    hour = int(hour) if isinstance(hour, (int, float)) and 0 <= hour <= 23 else DEFAULT_HOUR
    return select("Frequency") or "Weekly", day, hour


def is_due(props, now):
    frequency, day, hour = row_schedule(props)
    slot = last_slot(frequency, day, hour, now)
    last = parse_when(((props.get("Last sorted") or {}).get("date") or {}).get("start"))
    return slot is not None and (last is None or last < slot)


def notion_rows(config, only_due=False):
    """Ticked rows of the "Playlists to sort" Notion database: (notion, [(page id, name)]),
    only those whose scheduled time has come when only_due. None when no database is set."""
    raw = (config.get("notion_sort_db") or "").strip()
    if not raw:
        return None
    from notion_lists import Notion, database_id, plain
    token = os.environ.get("NOTION_TOKEN", "").strip()
    if not token:
        raise SystemExit("NOTION_TOKEN secret missing (needed to read notion_sort_db)")
    notion = Notion(token)
    body = {"filter": {"property": "Active", "checkbox": {"equals": True}}}
    now = datetime.now(PARIS)
    rows = []
    for page in notion.paginate("POST", f"/databases/{database_id(raw)}/query", body):
        props = page.get("properties", {})
        title = next((v.get("title") for v in props.values() if v.get("type") == "title"), [])
        name = plain(title).strip()
        if name and (not only_due or is_due(props, now)):
            rows.append((page["id"], name))
    return notion, rows


def count_due():
    """--due mode: writes due=<n> to GITHUB_OUTPUT (1 on a Notion error, so the sorting
    job runs and reports it). Doesn't touch the music service."""
    try:
        found = notion_rows(load_config(), only_due=True)
        count = 0 if found is None else len(found[1])
        log(f"{count} playlist(s) due for sorting")
    except Exception as e:
        count = 1
        log(f"Notion check failed ({type(e).__name__}: {str(e)[:150]}); the sorting job will report it")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"due={count}\n")


def notion_report(notion, page_id, copies=None, unsorted=0, error=None):
    if error:
        result = error
    else:
        result = " · ".join(f"{name}: {n}" for name, n in sorted(copies.items()))
        if unsorted:
            result += f" · not sorted (no year or preview): {unsorted}"
    props = {"Last sorted": {"date": {"start": datetime.now(PARIS).isoformat(timespec="minutes")}},
             "Result": {"rich_text": [{"text": {"content": result[:2000]}}]}}
    if copies is not None:
        props["Copies"] = {"number": len(copies)}
    try:
        notion.call("PATCH", f"/pages/{page_id}", {"properties": props})
    except Exception as e:
        log(f"   Could not write the result to Notion ({type(e).__name__})")


def main():
    config = load_config()
    if not os.environ.get("MUSIC_EMAIL") or not os.environ.get("MUSIC_PASSWORD"):
        raise SystemExit("MUSIC_EMAIL / MUSIC_PASSWORD secrets missing")
    wanted = os.environ.get("PLAYLISTS", "").strip()
    notion = None
    if wanted and wanted != "*":
        rows = [(None, n.strip()) for n in wanted.split(",") if n.strip()]
        log(f"{len(rows)} playlists from the workflow input")
    else:
        scheduled = os.environ.get("SCHEDULED", "").strip() == "1"
        found = notion_rows(config, only_due=scheduled)
        if found is None:
            rows = [(None, n.strip()) for n in config.get("sort_playlists", "").split(",")
                    if n.strip()]
            log(f"{len(rows)} playlists from config.txt")
        else:
            notion, rows = found
            log(f"{len(rows)} playlists " + ("due" if scheduled else "ticked")
                + " in the Notion database")
    if not rows:
        log("No playlist to sort")
        return
    config, rules, rules_notion, genre_db = load_rules(config)
    pattern = config.get("sort_playlist_name") or "{playlist} {mood} {period}"
    genres_seen = {}
    service = MusicService(os.environ["MUSIC_EMAIL"].strip(), os.environ["MUSIC_PASSWORD"].strip())
    service.login()
    log("Service login OK")
    playlists = service.list_playlists()
    mine = [p for p in playlists if service.user_id is None
            or (p.get("owner") or {}).get("id") in (None, service.user_id)]
    by_name = {flat(p.get("name")): p for p in mine}
    for page_id, name in rows:
        source = by_name.get(flat(name))
        if source is None:
            log("A playlist was not found among your playlists")
            if notion and page_id:
                notion_report(notion, page_id, error="Playlist not found (check the exact name)")
            continue
        try:
            copies, unsorted = sort_one(service, source, mine, config, pattern, rules, genres_seen)
        except Exception as e:
            log(f"   Sorting failed ({type(e).__name__}): {str(e)[:150]}")
            if notion and page_id:
                notion_report(notion, page_id, error=f"Error: {type(e).__name__}")
            continue
        if notion and page_id:
            notion_report(notion, page_id, copies, unsorted)
    if genres_seen:
        log("Genres met: " + ", ".join(f"{g} ({n})" for g, n in
                                       sorted(genres_seen.items(), key=lambda x: -x[1])))
        add_new_genres(rules_notion, genre_db, rules, genres_seen)


if __name__ == "__main__" and "--due" in sys.argv:
    count_due()
elif __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        log(str(e))
        sys.exit(1)
