import asyncio
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from shazamio import Shazam

# The music service is configured through secrets so that the repository never names it:
# MUSIC_API_URL (API base URL) and MUSIC_APP_ID (application ID).
API = os.environ.get("MUSIC_API_URL", "").strip().rstrip("/")
APP_ID = os.environ.get("MUSIC_APP_ID", "").strip()
NOTION_API = "https://api.notion.com/v1"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")
STREAM_URL = os.environ.get("STREAM_URL", "").strip()
STREAM_URL_BACKUP = os.environ.get("STREAM_URL_BACKUP", "").strip()   # optional
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "").strip().rstrip("/")  # optional
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "").strip()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
EMAIL = os.environ.get("MUSIC_EMAIL", "").strip()
PASSWORD = os.environ.get("MUSIC_PASSWORD", "").strip()
TEST_MODE = bool(os.environ.get("TEST_MINUTES", "").strip())


def check_secrets():
    if not STREAM_URL:
        raise SystemExit("STREAM_URL secret is missing or empty")
    if not API or not APP_ID:
        raise SystemExit("MUSIC_API_URL and/or MUSIC_APP_ID secrets are missing")
    if not EMAIL or not PASSWORD:
        raise SystemExit("MUSIC_EMAIL and/or MUSIC_PASSWORD secrets are missing")
SAMPLE_SECONDS = 15
CYCLE_SECONDS = 45
SPEECH_CYCLE_SECONDS = 90
MISSES_BEFORE_SPEECH = 4
SKIP_AFTER_MATCH = 75
MAX_SKIP = 300
STREAM_RETRY_SECONDS = 15
BOX_SET_TRACKS = 30        # albums with more tracks are treated as box sets/compilations
NEXT_SONG_MARGIN = 10
PARIS = ZoneInfo("Europe/Paris")
BAD_WORDS = re.compile(r"\b(live|karaoke|tribute|cover|instrumental|demo|remix|acoustic"
                       r"|refix|rework|mix|dub|edit)\b")
# Matching data, not prose: French and German words are kept on purpose so that
# French-language compilations are detected too.
COMPILATION_WORDS = re.compile(
    r"\b(best of|greatest hits|the hits|hits collection|collection|anthology|anthologie"
    r"|essential|essentials|the very best|very best|integrale|ultimate|compilation"
    r"|platinum|definitive|the singles|singles collection|le meilleur|tubes"
    r"|retrospective|b sides|g sides|rarities|raretes|now that s what i call|\d+ (titres|tracks|succes|chansons))\b")
VARIOUS_ARTISTS = {"various artists", "various", "artistes divers", "artistes varies",
                   "multi interpretes", "verschiedene interpreten", "compilation"}


def log(*args):
    print(datetime.now(PARIS).strftime("%H:%M:%S"), *args, flush=True)


# The repository is public, so are its logs. Track names and play times would let anyone
# identify the station by comparing them with its published playlist. They only go to
# private places (Notion, ntfy); set the VERBOSE_LOGS variable to "1" to print them too.
VERBOSE = os.environ.get("VERBOSE_LOGS", "").strip() == "1"


def log_detail(*args):
    if VERBOSE:
        log(*args)


_ntfy_failed = False


def notify(title, message, tags=(), priority=3):
    """Push notification through ntfy (secret NTFY_TOPIC: a topic name on ntfy.sh,
    or a full https://server/topic URL). Never stops the script if it fails."""
    global _ntfy_failed
    if not NTFY_TOPIC:
        return
    if NTFY_TOPIC.startswith("http"):
        server, _, topic = NTFY_TOPIC.rstrip("/").rpartition("/")
    else:
        server, topic = "https://ntfy.sh", NTFY_TOPIC
    try:
        r = requests.post(server, timeout=10, json={
            "topic": topic, "title": title[:250], "message": message[:3000],
            "tags": list(tags), "priority": priority})
        r.raise_for_status()
    except Exception as e:
        if not _ntfy_failed:
            log(f"ntfy notification failed ({type(e).__name__}), continuing without it")
            _ntfy_failed = True


def ping(kind=""):
    """Healthchecks-style ping (secret HEALTHCHECK_URL): "start", "" (success) or "fail".
    Skipped in test mode so that tests at odd hours do not confuse the schedule."""
    if not HEALTHCHECK_URL or TEST_MODE:
        return
    try:
        requests.get(f"{HEALTHCHECK_URL}/{kind}" if kind else HEALTHCHECK_URL, timeout=10)
    except Exception as e:
        log(f"Healthcheck ping failed ({type(e).__name__})")


def load_config():
    """Reads config.txt: 'key = value' lines; lines starting with # are ignored."""
    config = {}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                config[key.strip().lower()] = value.strip()
    except FileNotFoundError:
        raise SystemExit("config.txt not found") from None
    if not config.get("main_playlist"):
        raise SystemExit("config.txt: main_playlist is missing")
    return config


def flat(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def norm(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    text = re.sub(r"\s-\s[^-]*remaster[^-]*$", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


class MusicService:
    def __init__(self):
        self.token = None
        self.headers = {"X-App-Id": APP_ID, "User-Agent": "Mozilla/5.0"}

    def login(self):
        try:
            r = requests.post(
                API + "/user/login",
                params={"email": EMAIL, "password": PASSWORD, "app_id": APP_ID},
                headers=self.headers,
                timeout=20,
            )
            data = r.json()
            status = r.status_code
        except Exception:
            raise RuntimeError("Could not reach the service (network error or unreadable response)") from None
        if "user_auth_token" not in data:
            raise RuntimeError(f"Service login refused (HTTP {status})")
        self.token = data["user_auth_token"]

    def call(self, method, path, **params):
        for attempt in (1, 2):
            headers = {**self.headers, "X-User-Auth-Token": self.token}
            r = requests.request(method, API + path, params=params, headers=headers, timeout=20)
            if r.status_code == 401 and attempt == 1:
                self.login()
                continue
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                return {}

    def list_playlists(self):
        playlists, offset = [], 0
        while True:
            data = self.call("GET", "/playlist/getUserPlaylists", limit=500, offset=offset)
            items = (data.get("playlists") or data).get("items", [])
            playlists.extend(items)
            if len(items) < 500:
                return playlists
            offset += 500

    def rename_playlist(self, playlist_id, name):
        self.call("POST", "/playlist/update", playlist_id=playlist_id, name=name)

    def find_playlist(self, existing, name, old_names=()):
        """Finds the playlist; if only an old name exists, renames it."""
        playlist_id = existing.get(name.strip().lower())
        if playlist_id is not None:
            return playlist_id
        olds = [o.strip().lower() for o in old_names if o and o.strip()] + legacy_names(name)
        for old in olds:
            if old in existing:
                playlist_id = existing.pop(old)
                try:
                    self.rename_playlist(playlist_id, name)
                    log(f"Playlist renamed to '{name}'")
                except Exception as e:
                    log(f"Could not rename to '{name}' ({type(e).__name__}), old name kept")
                existing[name.strip().lower()] = playlist_id
                return playlist_id
        return None

    def create_playlist(self, name, description):
        data = self.call("POST", "/playlist/create", name=name, description=description,
                         is_public="0", is_collaborative="0")
        if "id" not in data:
            raise RuntimeError("Playlist creation: unexpected response")
        return data["id"]

    def playlist_track_ids(self, playlist_id):
        ids, offset = set(), 0
        while True:
            data = self.call("GET", "/playlist/get", playlist_id=playlist_id,
                             extra="tracks", limit=500, offset=offset)
            items = data.get("tracks", {}).get("items", [])
            ids.update(t["id"] for t in items)
            if len(items) < 500:
                return ids
            offset += 500

    def search(self, query):
        data = self.call("GET", "/catalog/search", query=query, limit=30)
        return data.get("tracks", {}).get("items", [])


def search_title(title):
    """Title for the service search, without version notes (radio edit, remaster...);
    otherwise the service ranks compilations containing that version first."""
    title = re.sub(r"\(.*?\)|\[.*?\]", " ", title or "")
    title = re.sub(r"\s-\s[^-]*(remaster|edit|version|mono|stereo)[^-]*$", " ", title,
                   flags=re.IGNORECASE)
    return " ".join(title.split())


def contains(a, b):
    """True if one contains the other as whole words (avoids 'a' inside 'bruno major')."""
    if not a or not b:
        return False
    return f" {a} " in f" {b} " or f" {b} " in f" {a} "


def score(track, artist, title):
    q_artist = norm((track.get("performer") or {}).get("name"))
    q_title = norm(track.get("title"))
    points = 0
    if q_artist == artist:
        points += 4
    elif contains(artist, q_artist):
        points += 2
    else:
        return -1
    if q_title == title:
        points += 4
    elif contains(title, q_title):
        points += 1
    else:
        return -1
    raw = " ".join([track.get("title") or "", track.get("version") or "",
                    (track.get("album") or {}).get("title") or ""]).lower()
    if BAD_WORDS.search(raw):
        points -= 3
    if track.get("streamable") is False:
        points -= 10
    return points


def album_bonus(track):
    """Order of preference: remastered version, then original album,
    then single, and last best-ofs and compilations."""
    album = track.get("album") or {}
    kind = str(album.get("release_type") or album.get("product_type") or "").lower()
    text = flat(" ".join([album.get("title") or "", album.get("version") or ""]))
    album_artist = flat((album.get("artist") or {}).get("name"))
    if (kind == "compilation" or album_artist in VARIOUS_ARTISTS or COMPILATION_WORDS.search(text)
            or (album.get("tracks_count") or 0) > BOX_SET_TRACKS):
        return -3
    bonus = 0
    if kind in ("single", "epsingle") or 0 < (album.get("tracks_count") or 0) <= 3:
        bonus -= 1
    remaster = flat(" ".join([track.get("title") or "", track.get("version") or "", text]))
    if "remaster" in remaster:
        bonus += 1
    return bonus


def album_date(track):
    album = track.get("album") or {}
    return album.get("release_date_original") or "9999"


def same_isrc(track, isrc):
    return bool(isrc) and (track.get("isrc") or "").strip().upper() == isrc


def pick(tracks, artist, title, isrc=None):
    """Keeps relevant results (score > 4), then prefers the original album,
    and on a tie the oldest release. A track with the ISRC given by the recognition
    is the same recording: it counts as relevant even if its names differ."""
    candidates = []
    for t in tracks:
        s = score(t, artist, title)
        if same_isrc(t, isrc) and t.get("streamable") is not False:
            s = max(s, 5)
        if s > 4:
            candidates.append((s + album_bonus(t), album_date(t), t))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c[0], c[1]))
    return candidates[0][2]


def find_match(service, artist, title, key, isrc=None):
    """Searches by names, plus by ISRC when the recognition gives one."""
    tracks = service.search(f"{artist} {search_title(title)}")
    if isrc:
        try:
            known = {t.get("id") for t in tracks}
            tracks += [t for t in service.search(isrc)
                       if same_isrc(t, isrc) and t.get("id") not in known]
        except Exception as e:
            log(f"   ISRC search failed ({type(e).__name__})")
    return pick(tracks, key[0], key[1], isrc)


def legacy_names(name):
    """Possible old names of a playlist, with the word test."""
    n = name.strip().lower()
    return ["test " + n, "test-" + n, "test - " + n, n + " test", n + "-test",
            n + " - test", n + "_test", n + " (test)"]


def text_prop(value):
    return {"rich_text": [{"text": {"content": (value or "")[:2000]}}]}


class Notion:
    """Writes to a Notion database: tracks missing from the service, or the play history."""

    def __init__(self, token, database_id):
        self.database_id = database_id.replace("-", "").strip()
        self.headers = {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28",
                        "Content-Type": "application/json"}

    def check(self):
        r = requests.get(f"{NOTION_API}/databases/{self.database_id}",
                         headers=self.headers, timeout=20)
        r.raise_for_status()

    def create(self, props):
        r = requests.post(f"{NOTION_API}/pages", headers=self.headers, timeout=20,
                          json={"parent": {"database_id": self.database_id}, "properties": props})
        r.raise_for_status()

    def add_history(self, artist, title, when, status, album, duration, shazam_url):
        props = {
            "Title": {"title": [{"text": {"content": (title or "?")[:2000]}}]},
            "Artist": text_prop(artist),
            "Played at": {"date": {"start": when.isoformat(timespec="seconds")}},
            "Status": {"select": {"name": status}},
        }
        if album:
            props["Album"] = text_prop(album)
        if duration:
            props["Duration (s)"] = {"number": int(duration)}
        if shazam_url:
            props["Shazam"] = {"url": shazam_url}
        self.create(props)

    def add_missing(self, artist, title, shazam_url, day):
        query = {"page_size": 1, "filter": {"and": [
            {"property": "Title", "title": {"equals": title[:2000]}},
            {"property": "Artist", "rich_text": {"equals": artist[:2000]}},
        ]}}
        r = requests.post(f"{NOTION_API}/databases/{self.database_id}/query",
                          json=query, headers=self.headers, timeout=20)
        r.raise_for_status()
        if r.json().get("results"):
            return False
        props = {
            "Title": {"title": [{"text": {"content": title[:2000]}}]},
            "Artist": {"rich_text": [{"text": {"content": artist[:2000]}}]},
            "Date": {"date": {"start": day.strftime("%Y-%m-%d")}},
        }
        if shazam_url:
            props["Shazam"] = {"url": shazam_url}
        self.create(props)
        return True


class StreamError(Exception):
    """Failure to read the stream; the message never contains the stream URL."""


FFMPEG_ERRORS = [
    (r"HTTP error (\d{3})", "stream server returned HTTP {0}"),
    (r"Server returned (\d{3})", "stream server returned HTTP {0}"),
    (r"Server returned 4\w\w|Forbidden|Unauthorized", "access to the stream denied"),
    (r"Server returned 5\w\w", "stream server error"),
    (r"Connection refused", "connection to the stream refused"),
    (r"Failed to resolve|Name or service not known|nodename nor servname",
     "stream server name not found"),
    (r"timed out|Connection timed out", "stream server not responding"),
    (r"Connection reset|End of file|Broken pipe", "stream cut off while reading"),
    (r"Invalid data|could not find codec|does not contain any stream",
     "stream content unreadable"),
]


def describe_ffmpeg_error(stderr):
    """Type of ffmpeg error, never echoing the raw text (it contains the URL)."""
    text = stderr or ""
    for pattern, message in FFMPEG_ERRORS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return message.format(*m.groups())
    return "could not read the stream (unknown cause)"


def capture(path, url=None):
    # ffmpeg output is captured and never printed: it contains the stream URL.
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
             "-i", url or STREAM_URL, "-t", str(SAMPLE_SECONDS),
             "-vn", "-acodec", "libmp3lame", path],
            check=True, timeout=SAMPLE_SECONDS + 30, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
    except subprocess.TimeoutExpired:
        raise StreamError("stream too slow (capture did not finish in time)") from None
    except subprocess.CalledProcessError as e:
        raise StreamError(describe_ffmpeg_error(e.stderr)) from None


def capture_any(path):
    """Main stream first, then the backup stream (secret STREAM_URL_BACKUP) if set.
    Returns "main" or "backup"; raises the main stream's error if both fail."""
    try:
        capture(path, STREAM_URL)
        return "main"
    except StreamError as main_error:
        if not STREAM_URL_BACKUP:
            raise
        try:
            capture(path, STREAM_URL_BACKUP)
            return "backup"
        except StreamError:
            raise main_error from None


def song_remaining(out, duration, sample_end):
    """Seconds left in the song, or None if it cannot be estimated."""
    try:
        offset = float(out["matches"][0]["offset"])
        duration = float(duration)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    return duration - (offset + SAMPLE_SECONDS) - (time.time() - sample_end)


def compute_wait(started, matched, remaining, misses=0):
    elapsed = time.time() - started
    if not matched:
        cycle = SPEECH_CYCLE_SECONDS if misses >= MISSES_BEFORE_SPEECH else CYCLE_SECONDS
        return cycle - elapsed
    if remaining is None:
        return CYCLE_SECONDS + SKIP_AFTER_MATCH - elapsed
    wait = remaining + NEXT_SONG_MARGIN
    return max(CYCLE_SECONDS - elapsed, min(wait, MAX_SKIP))


def daily_pattern(pattern):
    """Regex matching the names of the daily playlists, with the date as a group."""
    if not pattern or "{date}" not in pattern:
        return None
    before, _, after = pattern.partition("{date}")
    return re.compile(rf"^{re.escape(before.strip().lower())}\s*(\d{{4}}-\d{{2}}-\d{{2}})\s*"
                      rf"{re.escape(after.strip().lower())}$")


def old_daily_playlists(playlists, pattern, keep_days, today):
    """Daily playlists older than keep_days, as (id, date) pairs."""
    regex = daily_pattern(pattern)
    if regex is None or keep_days <= 0:
        return []
    old = []
    for p in playlists:
        m = regex.match((p.get("name") or "").strip().lower())
        if not m:
            continue
        try:
            day = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - day).days > keep_days:
            old.append((p["id"], day))
    return old


EARLY_START_MINUTES = 45   # a scheduled run may start up to this long before listen_start
LATEST_START = (23, 30)    # a scheduled run starting after this time does nothing


def parse_hhmm(value, default):
    m = re.fullmatch(r"\s*(\d{1,2})[:h](\d{2})\s*", value or "")
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return int(m.group(1)), int(m.group(2))
    return default


def compute_window(config):
    """Returns (listen_from, end). The job is scheduled a bit early to absorb GitHub's
    delays, then waits until listen_start (Paris time) before listening."""
    now = datetime.now(PARIS)
    test = os.environ.get("TEST_MINUTES", "").strip()
    if test:
        minutes = int(test) if test.isdigit() else 5
        log(f"Test mode: {minutes} minutes")
        return now, now + timedelta(minutes=minutes)
    hour, minute = parse_hhmm(config.get("listen_start"), (21, 0))
    listen_from = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    earliest = listen_from - timedelta(minutes=EARLY_START_MINUTES)
    latest = now.replace(hour=LATEST_START[0], minute=LATEST_START[1], second=0, microsecond=0)
    if now.weekday() >= 5 or not (earliest <= now < latest):
        log("Outside the listening window, nothing to do.")
        sys.exit(0)
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return listen_from, end


async def main():
    check_secrets()
    config = load_config()
    listen_from, end = compute_window(config)
    ping("start")
    wait = (listen_from - datetime.now(PARIS)).total_seconds()
    if wait > 0:
        log(f"Started early, waiting {int(wait // 60)} min before listening")
        await asyncio.sleep(wait)
    start = datetime.now(PARIS)
    service = MusicService()
    service.login()
    log("Service login OK")

    global CYCLE_SECONDS, SPEECH_CYCLE_SECONDS
    for key, name in (("listen_interval", "CYCLE_SECONDS"),
                      ("speech_interval", "SPEECH_CYCLE_SECONDS")):
        value = config.get(key, "")
        if value.isdigit() and int(value) >= 30:
            globals()[name] = int(value)
    log(f"Listening every {CYCLE_SECONDS} s, then every {SPEECH_CYCLE_SECONDS} s "
        f"after {MISSES_BEFORE_SPEECH} tries without music")
    value = config.get("confirmations", "2")
    confirmations = int(value) if value.isdigit() and int(value) >= 1 else 2
    log(f"A track is added after {confirmations} matching recognition(s)")
    if STREAM_URL_BACKUP:
        log("Backup stream configured")

    def open_notion(key, label):
        if not (NOTION_TOKEN and config.get(key)):
            log(f"Notion {label}: not configured (NOTION_TOKEN secret or {key} missing)")
            return None
        db = Notion(NOTION_TOKEN, config[key])
        try:
            db.check()
            log(f"Notion {label}: database reachable")
            return db
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (401, 403):
                why = "token refused"
            elif code == 404:
                why = "database not found or not shared with the connection"
            else:
                why = type(e).__name__ + (f" HTTP {code}" if code else "")
            log(f"Notion {label}: unreachable ({why})")
            return None

    notion = open_notion("notion_missing_db", "missing tracks")
    history = open_notion("notion_history_db", "history")

    main_name = config["main_playlist"]
    existing = {p["name"].strip().lower(): p["id"] for p in service.list_playlists()}
    main_id = service.find_playlist(existing, main_name,
                                  [config.get("previous_main_playlist", "")])
    if main_id is None:
        raise RuntimeError(f"Playlist '{main_name}' not found")
    targets = [{"label": "main", "name": main_name, "id": main_id,
                "present": service.playlist_track_ids(main_id), "added": 0}]

    def fill(pattern):
        return pattern.replace("{date}", f"{start:%Y-%m-%d}").replace("{month}", f"{start:%Y-%m}")

    extras = []
    if config.get("daily_playlist"):
        extras.append(("daily", fill(config["daily_playlist"]),
                       f"Tracks recognized automatically on {start:%m/%d/%Y}"))
    if config.get("monthly_playlist"):
        extras.append(("monthly", fill(config["monthly_playlist"]),
                       f"Tracks recognized automatically in {start:%m/%Y}"))
    for label, name, description in extras:
        try:
            playlist_id = service.find_playlist(existing, name)
            if playlist_id is None:
                playlist_id = service.create_playlist(name, description)
                present = set()
                log(f"Playlist created: {name}")
            else:
                present = service.playlist_track_ids(playlist_id)
            targets.append({"label": label, "name": name, "id": playlist_id,
                            "present": present, "added": 0})
        except Exception as e:
            log(f"Playlist '{name}' unavailable ({type(e).__name__}): {str(e)[:150]}")

    for t in targets:
        log(f"Playlist '{t['name']}' ({t['label']}): {len(t['present'])} tracks already present")
    if NTFY_TOPIC:
        log("ntfy notifications on")
    notify("Radio listening: listening started",
           f"Listening until {end:%H:%M}" + (" (test mode)" if os.environ.get("TEST_MINUTES", "").strip() else "")
           + f". Playlists: {', '.join(t['name'] for t in targets)}.",
           tags=["radio"])

    shazam = Shazam()
    seen = set()
    durations = {}
    recognized = errors_in_row = noted = misses = logged = unconfirmed = 0
    captures_ok = stream_errors = 0
    outage_start = 0.0
    on_backup = False
    pending = None      # first sighting of a track waiting for confirmation
    recap = []          # "symbol Artist - Title" lines for the end-of-session notification

    while datetime.now(PARIS) < end:
        started = time.time()
        matched = False
        confirming = False
        remaining = None
        try:
            sample_start = datetime.now(PARIS)
            source = capture_any("sample.mp3")
            if (source == "backup") != on_backup:
                on_backup = source == "backup"
                log("Using the backup stream" if on_backup else "Back on the main stream")
                notify("Radio listening: " + ("backup stream" if on_backup else "main stream back"),
                       "Main stream unavailable, listening to the backup stream."
                       if on_backup else "Listening to the main stream again.",
                       tags=["twisted_rightwards_arrows"])
            captures_ok += 1
            if stream_errors:
                outage = int(time.time() - outage_start)
                log(f"Stream back after {outage // 60} min {outage % 60:02d} s")
                notify("Radio listening: stream back",
                       f"Stream back after {outage // 60} min {outage % 60:02d} s.",
                       tags=["white_check_mark"])
            stream_errors = 0
            sample_end = time.time()
            out = await asyncio.wait_for(shazam.recognize("sample.mp3"), timeout=40)
            track = out.get("track")
            key = None
            if track:
                artist, title = track["subtitle"], track["title"]
                key = (norm(artist), norm(title))
            if pending and key != pending["key"]:
                pending["tries"] += 1
                if pending["tries"] >= 2:
                    unconfirmed += 1
                    log_detail(f"   Not confirmed, ignored: {pending['artist']} - {pending['title']}")
                    pending = None
            if track:
                recognized += 1
                matched = True
                if key not in seen:
                    isrc = (track.get("isrc") or "").strip().upper() or None
                    if pending and pending["key"] == key:
                        match, first = pending["match"], pending
                        pending = None
                    else:
                        log_detail(f"RECOGNIZED: {artist} - {title}")
                        match = find_match(service, artist, title, key, isrc)
                        first = {"sample_start": sample_start, "out": out}
                        if match is not None:
                            durations[key] = match.get("duration")
                        left = song_remaining(out, durations.get(key), sample_end)
                        # Ask for a second recognition, unless the song is about to end.
                        if confirmations > 1 and not (left is not None and left < CYCLE_SECONDS + 20):
                            if pending:
                                unconfirmed += 1
                            pending = {"key": key, "artist": artist, "title": title, "match": match,
                                       "tries": 0, "sample_start": sample_start, "out": out}
                            log_detail("   Waiting for a confirmation")
                            confirming = True
                    out_first = first["out"]
                    status, album_name, duration = "Not found", None, None
                    if confirming:
                        pass  # decided at the next recognition
                    elif match is None:
                        log_detail("   No match on the service")
                        if notion:
                            try:
                                if notion.add_missing(artist, title, track.get("url"), start):
                                    noted += 1
                                    log_detail("   Noted in Notion")
                                else:
                                    log_detail("   Already noted in Notion")
                            except Exception as e:
                                log(f"   Notion failure ({type(e).__name__})")
                    else:
                        durations[key] = match.get("duration")
                        done = []
                        for t in targets:
                            if match["id"] in t["present"]:
                                continue
                            try:
                                service.call("POST", "/playlist/addTracks", playlist_id=t["id"],
                                           track_ids=str(match["id"]), no_duplicate="true")
                                t["present"].add(match["id"])
                                t["added"] += 1
                                done.append(t["label"])
                            except Exception as e:
                                log(f"   Could not add ({t['label']}): {type(e).__name__}")
                        album = (match.get("album") or {}).get("title") or "?"
                        status = "Added" if done else "Already present"
                        album_name, duration = album, match.get("duration")
                        version = f" ({match['version']})" if match.get("version") else ""
                        label = (f"{match['performer']['name']} - {match['title'].strip()}{version}"
                                 f" [album: {album}] (id {match['id']})")
                        if done:
                            log_detail(f"   ADDED ({', '.join(done)}): {label}")
                        else:
                            log_detail("   Already present everywhere")
                if key not in seen and not confirming:
                    seen.add(key)
                    if status == "Added":
                        recap.append(f"+ {artist} - {title}")
                        notify(f"{artist} - {title}", f"Added to the playlists. Album: {album_name}",
                               tags=["musical_note"])
                    elif status == "Already present":
                        recap.append(f"= {artist} - {title}")
                        notify(f"{artist} - {title}",
                               f"Already in the playlists. Album: {album_name}", tags=["repeat"])
                    else:
                        recap.append(f"? {artist} - {title}")
                        notify(f"{artist} - {title}", "Not found on the service"
                               + (", noted in Notion" if notion else ""), tags=["warning"])
                    if history:
                        try:
                            offset = float(out_first["matches"][0]["offset"])
                        except (KeyError, IndexError, TypeError, ValueError):
                            offset = 0
                        song_start = first["sample_start"] - timedelta(seconds=max(0, offset))
                        try:
                            history.add_history(artist, title, song_start, status, album_name,
                                                duration, track.get("url"))
                            logged += 1
                        except Exception as e:
                            log(f"   Notion history failure ({type(e).__name__})")
                if not confirming:
                    remaining = song_remaining(out, durations.get(key), sample_end)
                    if remaining is not None:
                        log_detail(f"   Song estimated to end in {max(0, int(remaining))} s")
            errors_in_row = 0
            misses = 0 if matched else misses + 1
        except StreamError as e:
            # Stream unavailable: retry every 15 s until the end without giving up,
            # and print the message only about once every 10 minutes.
            stream_errors += 1
            if stream_errors == 1:
                outage_start = started
                notify("Radio listening: stream unavailable",
                       f"{e}. Retrying every {STREAM_RETRY_SECONDS} s.",
                       tags=["warning"], priority=4)
            if stream_errors == 1 or stream_errors % 40 == 0:
                log(f"Stream unavailable ({e}), retrying every {STREAM_RETRY_SECONDS} s"
                    + (f" [{stream_errors} tries]" if stream_errors > 1 else ""))
            wait = min(STREAM_RETRY_SECONDS - (time.time() - started),
                       (end - datetime.now(PARIS)).total_seconds())
            if wait > 0:
                await asyncio.sleep(wait)
            continue
        except Exception as e:
            errors_in_row += 1
            if isinstance(e, StreamError):
                detail = str(e)
            elif isinstance(e, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
                detail = "could not read the stream, or too slow"
            else:
                detail = str(e)[:150]
            log(f"Error ({type(e).__name__}): {detail}")
            misses += 1
            if errors_in_row >= 15:
                log("Too many consecutive errors, stopping.")
                ping("fail")
                notify("Radio listening: session failed",
                       f"Too many consecutive errors, stopping. Last one: {detail}",
                       tags=["rotating_light"], priority=5)
                sys.exit(1)
        if confirming:
            wait = CYCLE_SECONDS - (time.time() - started)
        else:
            wait = compute_wait(started, matched, remaining, misses)
        wait = min(wait, (end - datetime.now(PARIS)).total_seconds())
        if wait > 0:
            await asyncio.sleep(wait)

    log(f"Listening finished: {recognized} recognitions"
        + (f", {unconfirmed} not confirmed" if unconfirmed else ""))
    if notion:
        log(f"   Notion: {noted} tracks missing from the service noted")
    if history:
        log(f"   Notion: {logged} plays added to the history")
    for t in targets:
        log(f"   {t['name']}: {t['added']} tracks added")
    summary = (f"{recognized} recognitions, "
               + ", ".join(f"{t['added']} added to {t['name']}" for t in targets))
    if notion:
        summary += f", {noted} missing from the service"
    if unconfirmed:
        summary += f", {unconfirmed} not confirmed"
    cleanup_daily(service, config, start)
    if captures_ok == 0:
        ping("fail")
        notify("Radio listening: session failed",
               "The stream could never be read: check the STREAM_URL secret.",
               tags=["rotating_light"], priority=5)
        log("The stream could never be read: check the STREAM_URL secret.")
        sys.exit(1)
    lines = "\n".join(recap)
    if len(lines) > 3500:
        lines = lines[:3500].rsplit("\n", 1)[0] + "\n…"
    notify("Radio listening: listening finished",
           summary + "." + (f"\n\n{lines}" if lines else ""), tags=["checkered_flag"])
    ping()


def cleanup_daily(service, config, start):
    """Deletes the daily playlists older than keep_daily_days (config.txt, 0 = never)."""
    value = config.get("keep_daily_days", "30")
    keep = int(value) if value.isdigit() else 30
    if TEST_MODE or keep <= 0:
        return
    try:
        old = old_daily_playlists(service.list_playlists(), config.get("daily_playlist"),
                                  keep, start.date())
    except Exception as e:
        log(f"Daily playlist cleanup skipped ({type(e).__name__})")
        return
    deleted = 0
    for playlist_id, _ in old:
        try:
            service.call("POST", "/playlist/delete", playlist_id=playlist_id)
            deleted += 1
        except Exception as e:
            log(f"Could not delete an old daily playlist ({type(e).__name__})")
    if deleted:
        log(f"{deleted} daily playlist(s) older than {keep} days deleted")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit as e:
        if e.code not in (0, None) and not str(e.code).isdigit():
            ping("fail")
            notify("Radio listening: session failed", str(e.code), tags=["rotating_light"], priority=5)
        raise
    except Exception as e:
        ping("fail")
        notify("Radio listening: session failed", f"{type(e).__name__}: {str(e)[:300]}",
               tags=["rotating_light"], priority=5)
        raise
