"""Fills the Notion database "Radio - Artists" (notion_artists_db in config.txt): one row per
artist heard on the show, with a short biography, albums with their years, influences,
similar artists, country and picture. The Scriptable app shows it as the artist page.

Sources: the music service (biography, albums, similar artists, picture), MusicBrainz
(country), Wikidata ("influenced by"), Deezer (similar artists when the service has none).
Secrets: NOTION_TOKEN, MUSIC_EMAIL, MUSIC_PASSWORD, MUSIC_API_URL, MUSIC_APP_ID.
Environment: ARTISTS_LIMIT (new artists per run, default 120), REFRESH_EMPTY=1 (retry the
rows that have no biography and no albums). Nothing about the artists is printed (public
log): only counts.
"""
import html
import os
import re
import time

import requests

import radio_listen as r
from notion_backfill import pages, plain

WIKIDATA = "https://www.wikidata.org/w/api.php"
HEADERS = {"User-Agent": "listening-notes/1.0"}
MAX_BIO = 5800            # characters kept (a Notion rich text holds 2000 per part)
MAX_ALBUMS = 40
ROLE = re.compile(r"band|singer|musician|rapper|group|songwriter|duo|composer|guitarist|"
                  r"vocalist|dj|producer|artist|trio|orchestra|ensemble|pianist|drummer", re.I)


def strip_html(text):
    """Plain text of an HTML biography, paragraphs separated by a blank line."""
    text = re.sub(r"</\s*p\s*>|</\s*div\s*>", "\n\n", text or "", flags=re.I)
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def split_copyright(text):
    """(biography, copyright line): a trailing line starting with © / (c) / Copyright."""
    lines = (text or "").splitlines()
    source = []
    while lines and (not lines[-1].strip() or re.match(r"^\s*(©|\(c\)|copyright)", lines[-1], re.I)):
        line = lines.pop()
        if line.strip():
            source.insert(0, line.strip())
    return "\n".join(lines).strip(), " ".join(source)


def chunks(text, size=1900):
    """Rich-text parts of at most `size` characters, cut on spaces."""
    parts, text = [], (text or "").strip()
    while text:
        if len(text) <= size:
            parts.append(text)
            break
        cut = text.rfind(" ", 0, size)
        cut = cut if cut > size // 2 else size
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return parts


def rich(text):
    return {"rich_text": [{"text": {"content": p}} for p in chunks(text)[:3]]}


def album_year(item):
    for key in ("release_date_original", "release_date_stream", "release_date_download"):
        value = (item.get(key) or "")[:4]
        if value.isdigit():
            return int(value)
    stamp = item.get("released_at")
    if isinstance(stamp, (int, float)) and stamp > 0:
        return time.gmtime(stamp).tm_year
    return None


def album_lines(items, artist_id=None):
    """'1992 · Title' lines, oldest first: the artist's own albums (no singles, no
    compilations), one per title."""
    seen, rows = set(), []
    for it in items or []:
        if artist_id and str((it.get("artist") or {}).get("id") or artist_id) != str(artist_id):
            continue
        count = it.get("tracks_count") or 0
        if (it.get("product_type") or "album") in ("single", "epsingle") or 0 < count <= 3:
            continue
        title = (it.get("title") or "").strip()
        key = r.norm(title)
        year = album_year(it)
        if not title or key in seen:
            continue
        seen.add(key)
        rows.append((year or 9999, title))
    rows.sort()
    return [f"{y if y != 9999 else '?'} · {t}" for y, t in rows[:MAX_ALBUMS]]


def find_artist_id(service, name, link):
    """Artist id on the service: from the track already linked, else by search."""
    found = re.search(r"/track/(\d+)", link or "")
    if found:
        try:
            track = service.call("GET", "/track/get", track_id=found.group(1))
            artist_id = (track.get("performer") or {}).get("id") or \
                ((track.get("album") or {}).get("artist") or {}).get("id")
            if artist_id:
                return artist_id
        except Exception:
            pass
    try:
        data = service.call("GET", "/catalog/search", query=r.main_artist(name), limit=5)
        for a in (data.get("artists") or {}).get("items", []):
            if r.flat(a.get("name")) == r.flat(r.main_artist(name)):
                return a.get("id")
    except Exception:
        pass
    return None


def wikidata_influences(name, limit=6):
    """Artists that Wikidata lists as influences ("influenced by"), when it knows this one."""
    try:
        target = r.flat(r.main_artist(name))
        hits = requests.get(WIKIDATA, headers=HEADERS, timeout=10, params={
            "action": "wbsearchentities", "search": r.main_artist(name), "language": "en",
            "type": "item", "limit": 5, "format": "json"}).json().get("search", [])
        for hit in hits:
            if r.flat(hit.get("label")) != target or not ROLE.search(hit.get("description") or ""):
                continue
            ent = requests.get(WIKIDATA, headers=HEADERS, timeout=10, params={
                "action": "wbgetentities", "ids": hit["id"], "props": "claims",
                "format": "json"}).json()["entities"][hit["id"]]
            ids = []
            for c in (ent.get("claims") or {}).get("P737", []):
                try:
                    ids.append(c["mainsnak"]["datavalue"]["value"]["id"])
                except (KeyError, TypeError):
                    continue
            if not ids:
                return []
            labels = requests.get(WIKIDATA, headers=HEADERS, timeout=10, params={
                "action": "wbgetentities", "ids": "|".join(ids[:limit]), "props": "labels",
                "languages": "fr|en", "format": "json"}).json().get("entities", {})
            names = []
            for e in labels.values():
                lab = e.get("labels") or {}
                value = (lab.get("fr") or lab.get("en") or {}).get("value")
                if value:
                    names.append(value)
            return names
    except Exception:
        pass
    return []


def similar_names(service, artist_id, name, count=8):
    try:
        data = service.call("GET", "/artist/getSimilarArtists", artist_id=artist_id, limit=count)
        names = [a.get("name") for a in (data.get("artists") or {}).get("items", []) if a.get("name")]
        if names:
            return names[:count]
    except Exception:
        pass
    return r.similar_artists(name, count)


def build_row(service, name, link):
    """Properties of the row for one artist, plus the counts of what was found."""
    artist_id = find_artist_id(service, name, link)
    info, bio, source, albums, image = {}, "", "", [], ""
    if artist_id:
        try:
            info = service.call("GET", "/artist/get", artist_id=artist_id, extra="albums", limit=100)
        except Exception:
            info = {}
        biography = info.get("biography") or {}
        text = strip_html(biography.get("content") or biography.get("summary") or "")
        bio, source = split_copyright(text)
        source = source or (biography.get("source") or "")
        albums = album_lines(((info.get("albums") or {}).get("items")), artist_id)
        pic = info.get("image") or {}
        image = pic.get("large") or pic.get("extralarge") or pic.get("medium") or ""
    time.sleep(1.1)      # MusicBrainz asks for at most one request per second
    country = r.artist_country(name)
    similar = similar_names(service, artist_id, name) if artist_id else r.similar_artists(name, 8)
    influences = wikidata_influences(name)
    props = {"Name": {"title": [{"text": {"content": name[:2000]}}]}}
    if artist_id:
        props["Service ID"] = {"number": int(artist_id)}
    if country:
        props["Country"] = r.text_prop(country)
    if bio:
        props["Bio"] = rich(bio[:MAX_BIO])
    if source:
        props["Bio source"] = r.text_prop(source)
    if albums:
        props["Albums"] = rich("\n".join(albums))
    if influences:
        props["Influences"] = r.text_prop(", ".join(influences))
    if similar:
        props["Similar"] = r.text_prop(", ".join(similar))
    if image:
        props["Image"] = {"url": image}
    found = {"bio": bool(bio), "albums": bool(albums), "similar": bool(similar),
             "influences": bool(influences), "country": bool(country)}
    return props, found


def history_artists(history_db):
    """{flat name: (name, track link)} for every artist of the history."""
    out = {}
    for page in pages(history_db):
        props = page.get("properties") or {}
        name = plain(props.get("Artist"))
        if not name or ((props.get("Status") or {}).get("select") or {}).get("name") == "Remove":
            continue
        key = r.flat(name)
        link = (props.get("Link") or {}).get("url") or ""
        if key not in out or (link and not out[key][1]):
            out[key] = (name, link)
    return out


def existing_rows(artists_db):
    """{flat name: (page id, has content)} for the rows already in the database."""
    out = {}
    for page in pages(artists_db):
        props = page.get("properties") or {}
        has = bool(plain(props.get("Bio")) or plain(props.get("Albums")))
        out[r.flat(plain(props.get("Name")))] = (page["id"], has)
    return out


def main():
    if not r.NOTION_TOKEN:
        raise SystemExit("NOTION_TOKEN secret missing")
    config = r.load_config()
    if not config.get("notion_artists_db") or not config.get("notion_history_db"):
        raise SystemExit("notion_artists_db / notion_history_db missing in config.txt")
    limit = int(os.environ.get("ARTISTS_LIMIT") or 120)
    refresh = os.environ.get("REFRESH_EMPTY") == "1"
    service = r.MusicService()
    service.login()
    artists_db = r.Notion(r.NOTION_TOKEN, config["notion_artists_db"])
    artists_db.check()
    history_db = r.Notion(r.NOTION_TOKEN, config["notion_history_db"])
    heard = history_artists(history_db)
    rows = existing_rows(artists_db)
    todo = [(k, v) for k, v in heard.items()
            if k not in rows or (refresh and not rows[k][1])]
    print(f"Artists: {len(heard)} in the history, {len(rows)} already in the database, "
          f"{len(todo)} to do (at most {limit} now)", flush=True)
    counts = {"done": 0, "bio": 0, "albums": 0, "similar": 0, "influences": 0, "country": 0,
              "errors": 0}
    for key, (name, link) in todo[:limit]:
        try:
            props, found = build_row(service, name, link)
            if key in rows:
                requests.patch(f"{r.NOTION_API}/pages/{rows[key][0]}",
                               json={"properties": {k: v for k, v in props.items() if k != "Name"}},
                               headers=artists_db.headers, timeout=30).raise_for_status()
            else:
                artists_db.create(props)
            counts["done"] += 1
            for k, v in found.items():
                counts[k] += 1 if v else 0
        except Exception as e:
            counts["errors"] += 1
            print(f"   one artist failed ({type(e).__name__})", flush=True)
        time.sleep(0.35)
    print("Artists done: " + ", ".join(f"{k} {v}" for k, v in counts.items()), flush=True)


if __name__ == "__main__":
    main()
