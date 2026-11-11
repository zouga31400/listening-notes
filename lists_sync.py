"""Creates, completes or syncs music service playlists from text files.

File format:
    Playlist name               <- first non-empty line
    Artist - Title              <- one line per track
    1. Artist – Title           <- numbering, bullets, – and — dashes are accepted

Usage: python lists_sync.py lists/a.txt [lists/b.txt ...]
Secrets expected in the environment: MUSIC_EMAIL, MUSIC_PASSWORD.
The log never shows the email, the password, the token or the account's other
playlists.
"""

import os
import re
import sys
import time
import unicodedata

import requests

# The music service is configured through secrets so that the repository never names it:
# MUSIC_API_URL (API base URL) and MUSIC_APP_ID (application ID).
API = os.environ.get("MUSIC_API_URL", "").strip().rstrip("/")
APP_ID = os.environ.get("MUSIC_APP_ID", "").strip()
BATCH_SIZE = 50
SEARCH_PAUSE = 0.4
SEARCH_LIMIT = 30
BAD_WORDS = ("live", "karaoke", "tribute", "cover", "instrumental", "demo", "remix", "acoustic")
BAD_RE = re.compile(r"\b(" + "|".join(BAD_WORDS) + r")\b")
# Milder variants (remix is already penalized): mix, edit, re-recording...
MINOR_RE = re.compile(r"\b(mix|edit|re ?recorded|unplugged|session|rehearsal|mono)\b")
# Working versions that are never the studio track.
WORK_RE = re.compile(r"\b(riff tapes?|rough|outtakes?|alternate|alt|early|take \d+|work in progress|"
                     r"wip|sketch|guide vocal|bootleg|from the vault)\b")
# "version" values that still mean the studio track. Any other version is mildly penalized.
STUDIO_VERSION_RE = re.compile(r"^\s*(album|lp|original|studio|single|stereo|explicit|clean)?\s*"
                               r"(version|mix)?\s*$|remaster")
COMPILATION_RE = re.compile(r"\b(best of|greatest hits|hits|collection|compilation|anthology|essential)\b")

# Artist / title separator: a dash (-, –, —, ‐, ‒, −) surrounded by spaces.
SEP_SPACED = re.compile(r"\s+[-–—‐‒−]+\s+")
# Fallback: a tight long dash ("Artist—Title"), never a plain hyphen (Jay-Z).
SEP_TIGHT = re.compile(r"\s*[–—]\s*")
# Bullets at the start of a line.
BULLET = re.compile(r"^\s*[-–—*•·▪►▸‣+>]+\s+")
# Safe numbering: "1.", "1)", "(1)", "1:", "#1", "n°1".
NUMBER_SAFE = re.compile(r"^\s*(?:(?:#|n°\s*)\d{1,3}\s*[.):]?|\(?\d{1,3}\s*[.):]\)?)\s*", re.IGNORECASE)
# Numbering followed by a dash ("1 - "): removed only if a separator remains.
NUMBER_DASH = re.compile(r"^\s*#?\d{1,3}\s*[-–—]\s+")


def log(*args):
    print(*args, flush=True)


# Notifications for these playlists use their own ntfy topic (secret NTFY_LISTS_TOPIC:
# a topic name on ntfy.sh, or a full https://server/topic URL), separate from the
# radio's NTFY_TOPIC. Not set: no notification.
_ntfy_failed = False


def notify(title, message, tags=(), priority=3, click=None):
    """Sends a push notification; never stops the run if it fails."""
    global _ntfy_failed
    target = os.environ.get("NTFY_LISTS_TOPIC", "").strip()
    if not target:
        return
    if target.startswith("http"):
        server, _, topic = target.rstrip("/").rpartition("/")
    else:
        server, topic = "https://ntfy.sh", target
    try:
        r = requests.post(server, timeout=10, json={
            "topic": topic, "title": title[:250], "message": message[:3000],
            "tags": list(tags), "priority": priority, **({"click": click} if click else {})})
        r.raise_for_status()
    except Exception as e:
        if not _ntfy_failed:
            log(f"ntfy notification failed ({type(e).__name__}), continuing without it")
            _ntfy_failed = True


def notify_result(result, link=None):
    """Notification at the end of a playlist: what was added, removed, not found."""
    name = result["name"]
    album = result.get("album")
    if result.get("redirect") and album:
        notify(f"Album found: {album['title']}",
               f"No playlist created: this album has all {album['wanted']} songs of "
               f"\"{name}\".", tags=["cd"], click=web_link("album", album["id"]) or link)
        return
    action = ("created" if result.get("created") else
              "updated" if result.get("added") or result.get("removed") else "already up to date")
    lines = [f"{result.get('added', 0)} added, {result.get('already', 0)} already there, "
             f"{len(result.get('removed', []))} removed, {len(result.get('missing', []))} not found."]
    if result.get("missing"):
        lines.append("Not found: " + "; ".join(result["missing"][:10])
                     + (" ..." if len(result["missing"]) > 10 else ""))
    if album:
        lines.append(f"Album \"{album['title']}\": {album['covered']} of {album['wanted']} songs"
                     + (f" - {web_link('album', album['id'])}" if web_link("album", album["id"]) else ""))
    tags = ["warning"] if result.get("missing") else ["white_check_mark"]
    notify(f"Playlist {action}: {name}", "\n".join(lines), tags=tags, click=link)


def notify_error(name, error, link=None):
    notify(f"Playlist error: {name}", str(error), tags=["x"], priority=4, click=link)


def norm(text):
    """Normalization for comparisons: accents, case, punctuation, parentheses."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    words = text.split()
    if words and words[0] == "the" and len(words) > 1:
        words = words[1:]
    return " ".join(words)


# ---------------------------------------------------------------- reading

def split_line(line):
    """Returns (artist, title), or None if the line can't be read."""
    text = line.strip().lstrip("﻿")
    text = BULLET.sub("", text, count=1)
    text = NUMBER_SAFE.sub("", text, count=1)
    stripped = NUMBER_DASH.sub("", text, count=1)
    if stripped != text and (SEP_SPACED.search(stripped) or SEP_TIGHT.search(stripped)):
        text = stripped
    text = text.strip().strip('"«»“”').strip()
    for sep in (SEP_SPACED, SEP_TIGHT):
        parts = sep.split(text, maxsplit=1)
        if len(parts) == 2:
            artist, title = (p.strip().strip('"«»“”').strip() for p in parts)
            if norm(artist) and norm(title):
                return artist, title
    return None


def read_list(path):
    """Returns (name, [(line number, artist, title)], [(line number, unreadable text)])."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return parse_lines(f.read().splitlines())


# Optional lines right after the playlist name: the film or series the list comes from
# ("Film: Titanic (1997)") and the author written in the playlist description
# ("Author: Marie"; the default is playlist_author in config.txt).
FILM_LINE = re.compile(r"^\s*(?:film|movie|series|s[ée]rie)\s*:\s*(.+?)\s*$", re.IGNORECASE)
AUTHOR_LINE = re.compile(r"^\s*(?:author|auteur|by|par)\s*:\s*(.+?)\s*$", re.IGNORECASE)


def split_meta(lines):
    """(lines without the "Film:" / "Author:" lines, {"film": ..., "author": ...})."""
    meta, kept = {"film": None, "author": None}, []
    for line in lines:
        for key, pattern in (("film", FILM_LINE), ("author", AUTHOR_LINE)):
            m = pattern.match(line)
            if m and meta[key] is None:
                meta[key] = m.group(1)
                break
        else:
            kept.append(line)
    return kept, meta


def split_film(lines):
    """(lines without the "Film: ..." line, film name or None)."""
    kept, meta = split_meta(lines)
    return kept, meta["film"]


def parse_lines(lines, name=None):
    """Same as read_list, from a list of lines (name already known or not)."""
    entries, bad = [], []
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        if name is None:
            name = line
            continue
        if line.startswith("//") or line.startswith(";"):
            continue
        parsed = split_line(line)
        if parsed:
            entries.append((number, *parsed))
        else:
            bad.append((number, line))
    return name, entries, bad


# ---------------------------------------------------------------- the music service

class ServiceError(RuntimeError):
    pass


class MusicService:
    def __init__(self, email, password):
        self._email = email
        self._password = password
        self.token = None
        self.user_id = None
        self.headers = {"X-App-Id": APP_ID, "User-Agent": "Mozilla/5.0"}

    def login(self):
        try:
            r = requests.post(
                API + "/user/login",
                params={"email": self._email, "password": self._password, "app_id": APP_ID},
                headers=self.headers,
                timeout=20,
            )
            data = r.json()
            status = r.status_code
        except Exception:
            # Never pass the exception on: its message holds the URL with the credentials.
            raise ServiceError("Service login failed (network error or unreadable response)") from None
        if "user_auth_token" not in data:
            raise ServiceError(f"Service login refused (HTTP {status})")
        self.token = data["user_auth_token"]
        self.user_id = (data.get("user") or {}).get("id")

    def call(self, method, path, **params):
        relogged = False
        for attempt in range(4):
            headers = {**self.headers, "X-User-Auth-Token": self.token or ""}
            try:
                r = requests.request(method, API + path, params=params, headers=headers, timeout=30)
            except requests.RequestException:
                if attempt == 3:
                    raise ServiceError(f"Service unreachable ({path})") from None
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 401 and not relogged:
                relogged = True
                self.login()
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 3:
                    raise ServiceError(f"Service unavailable (HTTP {r.status_code} on {path})")
                time.sleep(2 ** (attempt + 1))
                continue
            if r.status_code >= 400:
                raise ServiceError(f"Request refused by the service (HTTP {r.status_code} on {path})")
            try:
                return r.json()
            except ValueError:
                return {}
        raise ServiceError(f"Request failed: {path}")

    def list_playlists(self):
        playlists, offset = [], 0
        while True:
            data = self.call("GET", "/playlist/getUserPlaylists", limit=500, offset=offset)
            items = (data.get("playlists") or data).get("items", [])
            playlists.extend(items)
            if len(items) < 500:
                return playlists
            offset += 500

    def find_playlist(self, name):
        """The account's playlist with this name (case ignored), or None."""
        wanted = name.strip().casefold()
        for p in self.list_playlists():
            owner = (p.get("owner") or {}).get("id")
            if self.user_id and owner and str(owner) != str(self.user_id):
                continue
            if (p.get("name") or "").strip().casefold() == wanted:
                return p
        return None

    def create_playlist(self, name, description=None):
        data = self.call("POST", "/playlist/create", name=name,
                         description=description or description_for(),
                         is_public="0", is_collaborative="0")
        if "id" not in data:
            raise ServiceError("Playlist creation: unexpected response")
        return data["id"]

    def set_description(self, playlist_id, description):
        self.call("POST", "/playlist/update", playlist_id=playlist_id, description=description)

    def playlist_tracks(self, playlist_id):
        """Entries of the playlist, in order (each has 'id' and 'playlist_track_id')."""
        tracks, offset = [], 0
        while True:
            data = self.call("GET", "/playlist/get", playlist_id=playlist_id,
                             extra="tracks", limit=500, offset=offset)
            items = (data.get("tracks") or {}).get("items", [])
            tracks.extend(t for t in items if "id" in t)
            if len(items) < 500:
                return tracks
            offset += 500

    def playlist_track_ids(self, playlist_id):
        return {t["id"] for t in self.playlist_tracks(playlist_id)}

    def remove_tracks(self, playlist_id, playlist_track_ids):
        for i in range(0, len(playlist_track_ids), BATCH_SIZE):
            batch = playlist_track_ids[i:i + BATCH_SIZE]
            self.call("POST", "/playlist/deleteTracks", playlist_id=playlist_id,
                      playlist_track_ids=",".join(str(t) for t in batch))

    def search(self, query):
        data = self.call("GET", "/catalog/search", query=query, limit=SEARCH_LIMIT)
        return (data.get("tracks") or {}).get("items", [])

    def add_tracks(self, playlist_id, track_ids):
        added = 0
        for i in range(0, len(track_ids), BATCH_SIZE):
            batch = track_ids[i:i + BATCH_SIZE]
            self.call("POST", "/playlist/addTracks", playlist_id=playlist_id,
                      track_ids=",".join(str(t) for t in batch), no_duplicate="true")
            added += len(batch)
        return added


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")


def config_value(key):
    """One 'key = value' line of config.txt (lines starting with # are ignored)."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip().lower() == key:
                        return v.strip()
    except FileNotFoundError:
        pass
    return ""


# Default author: the PLAYLIST_AUTHOR secret (kept out of this public repository and
# masked in the logs). Without it, the description doesn't name anyone.
NO_AUTHOR_DESCRIPTION = "Created from a list"


def description_for(author=None):
    """Playlist description: "Created by <author>" (request's author, else the
    PLAYLIST_AUTHOR secret), or "Created from a list" when there is none."""
    author = " ".join((author or os.environ.get("PLAYLIST_AUTHOR", "")).split())[:60]
    return f"Created by {author}" if author else NO_AUTHOR_DESCRIPTION


# Playlists created by this tool (current or older French description). Only these are
# kept in sync with their list: tracks that are no longer chosen get removed.
# "Created by lists_sync.py" (older playlists carry the previous script name, list_<name>.py).
MANAGED_RE = re.compile(r"\b(?:lists_sync|list(?:e|es|s)?_\w+)\.py\b", re.IGNORECASE)
# Current description: exactly "Created by <author>" on one short line.
MANAGED_AUTHOR_RE = re.compile(r"^\s*(?:Created by [^\n]{1,60}|Created from a list)\s*$")


def is_managed(playlist):
    description = (playlist or {}).get("description") or ""
    return bool(MANAGED_RE.search(description) or MANAGED_AUTHOR_RE.match(description))


def label(track):
    """'Performer - Title (version)' for logs and reports."""
    text = f"{(track.get('performer') or {}).get('name')} - {track.get('title')}"
    if track.get("version"):
        text += f" ({track['version']})"
    return " ".join(text.split())


# ---------------------------------------------------------------- picking a track

def contains(a, b):
    """True if one normalized string contains the other, on whole words."""
    return bool(a and b) and (f" {a} " in f" {b} " or f" {b} " in f" {a} ")


def artist_names(track):
    """The track's performer. The album artist is only a fallback: on a tribute album
    it names the artist being covered, not the performer (e.g. a cover of Enter Sandman)."""
    performer = norm((track.get("performer") or {}).get("name"))
    if performer:
        return [performer]
    album_artist = norm(((track.get("album") or {}).get("artist") or {}).get("name"))
    return [album_artist] if album_artist else []


def score(track, artist, title, request_raw):
    """Scores a result; None if the artist or the title doesn't match."""
    if track.get("streamable") is False:
        return None
    names = artist_names(track)
    if artist in names:
        points = 10
    elif any(contains(artist, n) for n in names):
        points = 6
    else:
        return None

    full_title = track.get("title") or ""
    q_title = norm(full_title)
    if q_title == title:
        points += 10
    elif contains(title, q_title):
        points += 4
    else:
        return None

    raw = (full_title + " " + (track.get("version") or "")).lower()
    for word in set(BAD_RE.findall(raw)):
        if not re.search(r"\b" + word + r"\b", request_raw):
            points -= 6
    for word in set(MINOR_RE.findall(raw)):
        if not re.search(r"\b" + word + r"\b", request_raw):
            points -= 2
    for word in set(WORK_RE.findall(raw)):
        if not re.search(r"\b" + word + r"\b", request_raw):
            points -= 6
    version = (track.get("version") or "").lower()
    if version and not STUDIO_VERSION_RE.search(version) and version not in request_raw:
        points -= 1
    album = ((track.get("album") or {}).get("title") or "").lower()
    if BAD_RE.search(album) and not BAD_RE.search(request_raw):
        points -= 2
    if COMPILATION_RE.search(album):
        points -= 0.5
    if "remaster" in raw:
        points -= 0.3
    return points


def best_match(service, artist, title):
    n_artist, n_title = norm(artist), norm(title)
    request_raw = title.lower()  # only words in the requested title lift the penalty
    clean_title = re.sub(r"\(.*?\)|\[.*?\]", " ", title).strip() or title
    queries = [f"{artist} {title}"]
    if clean_title != title:
        queries.append(f"{artist} {clean_title}")
    queries.append(f"{n_artist} {n_title}")
    seen_queries = set()
    for query in queries:
        if query.lower() in seen_queries:
            continue
        seen_queries.add(query.lower())
        best, best_score = None, None
        for position, track in enumerate(service.search(query)):
            s = score(track, n_artist, n_title, request_raw)
            if s is None:
                continue
            s -= position * 0.01  # on a tie, keep the service's relevance order
            if best_score is None or s > best_score:
                best, best_score = track, s
        time.sleep(SEARCH_PAUSE)
        if best is not None:
            return best
    return None


# ---------------------------------------------------------------- soundtrack albums

SOUNDTRACK_RE = re.compile(r"soundtrack|motion picture|original score|bande originale|\bost\b|"
                           r"music from|songs from|from the (?:film|movie|series)", re.IGNORECASE)


def web_link(kind, item_id):
    web = os.environ.get("MUSIC_WEB_URL", "").strip().rstrip("/")
    return f"{web}/{kind}/{item_id}" if web and item_id else None


def soundtrack_album(service, film, entries):
    """Soundtrack album of the film that holds the most requested songs, or None.
    Returns {id, title, artist, tracks_count, covered, wanted, missing}."""
    film_n = norm(film)
    if not film_n:
        return None
    wanted = list(dict.fromkeys((a, t) for _, a, t in entries))
    seen, best = set(), None
    for query in (f"{film} soundtrack", f"{film} original motion picture soundtrack", film):
        data = service.call("GET", "/catalog/search", query=query, limit=10)
        time.sleep(SEARCH_PAUSE)
        for item in (data.get("albums") or {}).get("items", []):
            album_id, title = item.get("id"), item.get("title") or ""
            genre = ((item.get("genre") or {}).get("name") or "").lower()
            if not album_id or album_id in seen or len(seen) >= 6:
                continue
            if item.get("streamable") is False or not contains(film_n, norm(title)):
                continue
            if not (SOUNDTRACK_RE.search(f"{title} {item.get('version') or ''}")
                    or any(w in genre for w in ("soundtrack", "bande", "film"))):
                continue
            seen.add(album_id)
            album = service.call("GET", "/album/get", album_id=album_id)
            tracks = (album.get("tracks") or {}).get("items", [])
            if not tracks:
                continue
            missing = [f"{a} - {t}" for a, t in wanted
                       if not any(score(tr, norm(a), norm(t), t.lower()) is not None for tr in tracks)]
            found = {"id": album_id, "title": title,
                     "artist": ((album.get("artist") or item.get("artist") or {}).get("name") or ""),
                     "tracks_count": len(tracks), "covered": len(wanted) - len(missing),
                     "wanted": len(wanted), "missing": missing}
            if best is None or (found["covered"], -found["tracks_count"]) > \
                    (best["covered"], -best["tracks_count"]):
                best = found
    return best


def album_holds_list(album):
    """The album replaces the playlist: it has every requested song, and the songs are at
    least half of the album (not mostly the score)."""
    return (album["wanted"] > 0 and album["covered"] == album["wanted"]
            and album["covered"] * 2 >= album["tracks_count"])


def album_worth_mentioning(album):
    return album["covered"] > 0 and album["covered"] * 2 >= album["wanted"]


# ---------------------------------------------------------------- processing

def summary(text):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def process(service, path):
    log(f"=== File: {path}")
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        lines, meta = split_meta(f.read().splitlines())
    name, entries, bad = parse_lines(lines)
    if not name:
        log("  Empty file: skipped.")
        return None
    return build_playlist(service, name, entries, bad, f"File: `{path}`",
                          film=meta["film"], author=meta["author"])


def build_playlist(service, name, entries, bad, source, film=None, author=None):
    """Creates, completes or syncs the playlist; returns the report as a dictionary.

    For a playlist created by this tool, the list is the reference: tracks that are
    no longer chosen (wrong version, line removed) and duplicates are removed.
    Other playlists with the same name are only completed, never trimmed.
    """
    result = {"name": name, "added": 0, "already": 0, "removed": [], "missing": [],
              "bad": bad, "tracks": [], "playlist_id": None, "created": False, "synced": False,
              "album": None, "redirect": False}
    log(f"  Playlist: {name}")
    for number, line in bad:
        log(f"  Line {number} unreadable (expected \"Artist - Title\"): {line}")
    if not entries:
        log("  No readable track: nothing to do.")
        return result

    soundtrack = None
    if film:
        try:
            soundtrack = soundtrack_album(service, film, entries)
        except ServiceError as e:
            log(f"  Soundtrack album search failed ({e}): building the playlist.")
        if soundtrack:
            log(f"  Soundtrack album \"{soundtrack['title']}\": {soundtrack['covered']} of the "
                f"{soundtrack['wanted']} songs, {soundtrack['tracks_count']} tracks in all.")
        if soundtrack and album_holds_list(soundtrack):
            log("  The album has every song of the list: no playlist created.")
            result.update(album=soundtrack, redirect=True)
            summary(f"### {name}\n\n- {source}\n- No playlist: album **{soundtrack['title']}** "
                    f"has all {soundtrack['wanted']} songs.\n")
            return result
        if soundtrack and not album_worth_mentioning(soundtrack):
            soundtrack = None

    description = description_for(author)
    existing = service.find_playlist(name)
    managed = existing is None or is_managed(existing)
    if existing:
        playlist_id = existing["id"]
        current = service.playlist_tracks(playlist_id)
        mode = "syncing it with the list" if managed else "completing it (not created by this tool)"
        log(f"  Existing playlist: {mode} ({len(current)} tracks in it).")
        if managed and (existing.get("description") or "").strip() != description:
            try:
                service.set_description(playlist_id, description)
                log("  Description updated.")   # not printed: it may hold the author's name
            except ServiceError as e:
                log(f"  Description not updated: {e}")
    else:
        playlist_id = service.create_playlist(name, description)
        current = []
        log("  Playlist created (private).")
    present = {t["id"] for t in current}

    to_add, chosen, missing, already, seen, rows = [], set(), [], 0, set(), []
    for number, artist, title in entries:
        key = (norm(artist), norm(title))
        if key in seen:
            log(f"  Line {number} is a duplicate in the file: {artist} - {title}")
            continue
        seen.add(key)
        track = best_match(service, artist, title)
        if track is None:
            missing.append((artist, title))
            rows.append({"line": f"{artist} - {title}", "status": "not found"})
            log(f"  Not found: {artist} - {title}")
            continue
        album = (track.get("album") or {}).get("title") or ""
        row = {"line": f"{artist} - {title}", "found": label(track), "album": album}
        if track["id"] in present or track["id"] in chosen:
            already += 1
            row["status"] = "present"
            log(f"  Already present: {label(track)}")
        else:
            to_add.append(track["id"])
            row["status"] = "added"
            log(f"  Found: {label(track)}")
        chosen.add(track["id"])
        rows.append(row)

    added = service.add_tracks(playlist_id, to_add) if to_add else 0

    removed = []
    if existing and managed:
        kept, drop = set(), []
        # A line not found this time keeps the track it may have matched before.
        unresolved = [(norm(a), norm(ti), ti.lower()) for a, ti in missing]
        for t in current:
            if t["id"] in chosen and t["id"] not in kept:
                kept.add(t["id"])
                continue
            if t["id"] not in chosen and any(score(t, na, nt, raw) is not None
                                             for na, nt, raw in unresolved):
                continue
            if t.get("playlist_track_id"):
                drop.append(t)
        if drop:
            try:
                service.remove_tracks(playlist_id, [t["playlist_track_id"] for t in drop])
                removed = [label(t) + (" (duplicate)" if t["id"] in chosen else "") for t in drop]
                for r in removed:
                    log(f"  Removed: {r}")
            except ServiceError as e:
                log(f"  Could not remove {len(drop)} track(s): {e}")

    missing_lines = [f"{a} - {t}" for a, t in missing]
    log(f"  Report \"{name}\": {added} track(s) added, {already} already present, "
        f"{len(removed)} removed, {len(missing)} not found, {len(bad)} unreadable line(s).")
    if missing_lines:
        log("  Tracks not found:")
        for m in missing_lines:
            log(f"    - {m}")

    md = [f"### {name}", "", f"- {source}",
          f"- Added: **{added}**", f"- Already present: {already}",
          f"- Removed: {len(removed)}"]
    md += [f"  - {r}" for r in removed]
    md.append(f"- Not found: {len(missing)}")
    md += [f"  - {m}" for m in missing_lines]
    if bad:
        md.append(f"- Unreadable lines: {len(bad)}")
        md += [f"  - line {n}: {t}" for n, t in bad]
    summary("\n".join(md) + "\n")
    result.update(added=added, already=already, removed=removed, missing=missing_lines,
                  tracks=rows, playlist_id=playlist_id, created=not existing,
                  synced=bool(existing and managed), album=soundtrack)
    return result


def connect():
    """Logs in to the music service from the secrets; returns None (and logs why) on failure."""
    email = os.environ.get("MUSIC_EMAIL", "").strip()
    password = os.environ.get("MUSIC_PASSWORD", "").strip()
    if not email or not password:
        log("MUSIC_EMAIL and/or MUSIC_PASSWORD secrets are missing.")
        return None
    if not API or not APP_ID:
        log("MUSIC_API_URL and/or MUSIC_APP_ID secrets are missing.")
        return None
    service = MusicService(email, password)
    try:
        service.login()
    except ServiceError as e:
        log(str(e))
        return None
    log("Logged in to the service.")
    return service


def main(paths):
    if not paths:
        log("Usage: python lists_sync.py lists/file.txt [...]")
        return 2
    service = connect()
    if service is None:
        return 1

    failures = 0
    for path in paths:
        if not os.path.isfile(path):
            log(f"=== File not found: {path}")
            failures += 1
            continue
        try:
            result = process(service, path)
            if result and (result.get("playlist_id") or result.get("redirect")):
                web = os.environ.get("MUSIC_WEB_URL", "").strip().rstrip("/")
                notify_result(result, f"{web}/playlist/{result['playlist_id']}" if web else None)
        except ServiceError as e:
            failures += 1
            log(f"  Error: {e}")
            notify_error(path, e)
        except Exception as e:  # generic message: no detail that could hold a secret
            failures += 1
            log(f"  Unexpected error ({type(e).__name__}).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
