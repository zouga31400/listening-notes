"""Completes the pages already in the Notion history database (run once by hand):
album cover (page cover; page icons are removed), link to the track on the service, YouTube link,
year, genre and decade. Pages that already have a link and a cover are left as they are.

Secrets: NOTION_TOKEN, MUSIC_EMAIL, MUSIC_PASSWORD, MUSIC_API_URL, MUSIC_APP_ID,
MUSIC_WEB_URL. Nothing about the tracks is printed (public log): only counts.
"""
import re
import time
from datetime import datetime, timedelta

import requests

import radio_listen as r


def plain(prop):
    """Text of a title / rich_text property."""
    items = (prop or {}).get("title") or (prop or {}).get("rich_text") or []
    return "".join(i.get("plain_text", "") for i in items).strip()


def pages(db):
    """Every page of the database."""
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = requests.post(f"{r.NOTION_API}/databases/{db.database_id}/query",
                             json=body, headers=db.headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        yield from data.get("results", [])
        if not data.get("has_more"):
            return
        cursor = data.get("next_cursor")


def evening_date(start):
    """Date of the show evening of a play time (an evening runs until 6 a.m., Paris time)."""
    when = datetime.fromisoformat(start.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=r.PARIS)
    return (when.astimezone(r.PARIS) - timedelta(hours=6)).strftime("%Y-%m-%d")


def backfill(db, service, counts, daily_ids=None, pattern="", month_pattern=""):
    for page in pages(db):
        props = page.get("properties") or {}
        title, artist = plain(props.get("Title")), plain(props.get("Artist"))
        if not title:
            continue
        year = (props.get("Year") or {}).get("number")
        has_link = bool((props.get("Link") or {}).get("url"))
        has_cover = bool(page.get("cover")) and bool((props.get("Cover") or {}).get("files"))
        update, cover = {}, ""

        if service and not (has_link and has_cover):
            try:
                match = r.find_match(service, artist, title, (r.norm(artist), r.norm(title)))
            except Exception:
                match = None
                counts["errors"] += 1
            if match:
                counts["matched"] += 1
                cover = r.cover_url(match, {})
                link = r.track_link(match)
                if link and not has_link:
                    update["Link"] = {"url": link}
                info = r.track_info(match, {})
                if not year and info.get("year"):
                    year = info["year"]
                    update["Year"] = {"number": year}
                if info.get("genre") and not plain(props.get("Genre")):
                    update["Genre"] = r.text_prop(info["genre"])
        if year and not (props.get("Decade") or {}).get("select"):
            update["Decade"] = {"select": {"name": r.decade_label(int(year))}}
        if not (props.get("YouTube") or {}).get("url"):
            update["YouTube"] = {"url": r.youtube_link(artist, title)}

        # Album link and place of the track in its album, from the track already linked.
        link_url = (props.get("Link") or {}).get("url") or (update.get("Link") or {}).get("url") or ""
        if (service and "Album link" in db.properties and link_url
                and not (props.get("Album link") or {}).get("url")):
            found = re.search(r"/track/(\d+)", link_url)
            if found:
                try:
                    full = service.call("GET", "/track/get", track_id=found.group(1))
                    if r.album_link(full):
                        update["Album link"] = {"url": r.album_link(full)}
                    if r.track_position(full) and not plain(props.get("Track")):
                        update["Track"] = r.text_prop(r.track_position(full))
                except Exception:
                    counts["errors"] += 1

        # Link of the daily playlist of that evening, while that playlist still exists.
        started = ((props.get("Played at") or {}).get("date") or {}).get("start")
        if (daily_ids and pattern and started and "Day playlist" in db.properties
                and not (props.get("Day playlist") or {}).get("url")):
            try:
                name = pattern.replace("{date}", evening_date(started)).strip().lower()
                if daily_ids.get(name):
                    update["Day playlist"] = {"url": r.playlist_link(daily_ids[name])}
            except Exception:
                counts["errors"] += 1

        # Link of the monthly playlist of that evening (kept, unlike the daily ones).
        if (daily_ids and month_pattern and started and "Month playlist" in db.properties
                and not (props.get("Month playlist") or {}).get("url")):
            try:
                name = month_pattern.replace("{month}", evening_date(started)[:7]).strip().lower()
                if daily_ids.get(name):
                    update["Month playlist"] = {"url": r.playlist_link(daily_ids[name])}
            except Exception:
                counts["errors"] += 1

        update = {k: v for k, v in update.items() if k in db.properties}
        body = {}
        if update:
            body["properties"] = update
        if not cover and page.get("cover"):  # cover set on an earlier run
            cover = ((page["cover"].get("external") or {}).get("url")) or ""
        if cover and not has_cover:
            body["cover"] = {"type": "external", "external": {"url": cover}}
            if "Cover" in db.properties:
                body.setdefault("properties", {})["Cover"] = r.cover_prop(cover)
        if page.get("icon"):  # no icon next to the title in lists: the cover is enough
            body["icon"] = None
        if not body:
            continue
        resp = requests.patch(f"{r.NOTION_API}/pages/{page['id']}", json=body,
                              headers=db.headers, timeout=30)
        if resp.ok:
            counts["updated"] += 1
            counts["covers"] += 1 if "cover" in body else 0
        else:
            counts["errors"] += 1
        time.sleep(0.35)  # Notion allows about 3 requests per second


def main():
    if not r.NOTION_TOKEN:
        raise SystemExit("NOTION_TOKEN secret missing")
    config = r.load_config()
    service = None
    if r.API and r.APP_ID and r.EMAIL and r.PASSWORD:
        service = r.MusicService()
        service.login()
    else:
        print("Service secrets missing: covers and links are skipped", flush=True)
    for key, label, wanted in (("notion_history_db", "history", r.HISTORY_PROPERTIES),
                               ("notion_missing_db", "missing tracks", r.MISSING_PROPERTIES)):
        if not config.get(key):
            continue
        db = r.Notion(r.NOTION_TOKEN, config[key])
        db.check(wanted)
        counts = {"matched": 0, "updated": 0, "covers": 0, "errors": 0}
        # Missing tracks are not on the service: only decade and YouTube for them.
        daily_ids = None
        if key == "notion_history_db" and service:
            daily_ids = {p["name"].strip().lower(): p["id"] for p in service.list_playlists()}
        backfill(db, service if key == "notion_history_db" else None, counts,
                 daily_ids, config.get("daily_playlist", ""), config.get("monthly_playlist", ""))
        print(f"Notion {label}: {counts['updated']} page(s) completed, {counts['covers']} "
              f"cover(s), {counts['matched']} found on the service, {counts['errors']} error(s)",
              flush=True)


if __name__ == "__main__":
    main()
