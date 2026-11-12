"""Completes the pages already in the Notion history database (run once by hand):
album cover (page cover and icon), link to the track on the service, YouTube link,
year, genre and decade. Pages that already have a link and a cover are left as they are.

Secrets: NOTION_TOKEN, MUSIC_EMAIL, MUSIC_PASSWORD, MUSIC_API_URL, MUSIC_APP_ID,
MUSIC_WEB_URL. Nothing about the tracks is printed (public log): only counts.
"""
import time

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


def backfill(db, service, counts):
    for page in pages(db):
        props = page.get("properties") or {}
        title, artist = plain(props.get("Title")), plain(props.get("Artist"))
        if not title:
            continue
        year = (props.get("Year") or {}).get("number")
        has_link = bool((props.get("Link") or {}).get("url"))
        has_cover = bool(page.get("cover"))
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

        update = {k: v for k, v in update.items() if k in db.properties}
        body = {}
        if update:
            body["properties"] = update
        if cover and not has_cover:
            body["cover"] = {"type": "external", "external": {"url": cover}}
            body["icon"] = {"type": "external", "external": {"url": cover}}
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
        backfill(db, service if key == "notion_history_db" else None, counts)
        print(f"Notion {label}: {counts['updated']} page(s) completed, {counts['covers']} "
              f"cover(s), {counts['matched']} found on the service, {counts['errors']} error(s)",
              flush=True)


if __name__ == "__main__":
    main()
