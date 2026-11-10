"""Playlist analysis: suggests similar tracks in a suggestions playlist.

For each chosen playlist (config.txt: analyze_playlists, or the workflow input),
the script:
  1. reads its tracks (artists, genres, eras);
  2. looks for related artists (similar artists according to the service, and the
     "dynamic/suggest" radio engine when it responds);
  3. keeps each one's most-played tracks (service ranking, or Deezer if the service
     refuses), in the original album version, without tracks already present,
     live versions, remixes...;
  4. ranks the candidates and adds the best ones to a private playlist
     "Suggestions <name>", never touching the original playlist.
Running the script again adds new suggestions without duplicates, from artists
not suggested before.
"""
import os
import random
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# The music service is configured through secrets so that the repository never names it:
# MUSIC_API_URL (API base URL) and MUSIC_APP_ID (application ID).
API = os.environ.get("MUSIC_API_URL", "").strip().rstrip("/")
APP_ID = os.environ.get("MUSIC_APP_ID", "").strip()
DEEZER = "https://api.deezer.com"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")
PARIS = ZoneInfo("Europe/Paris")
BAD_WORDS = re.compile(r"\b(live|karaoke|tribute|cover|instrumental|demo|remix|acoustic"
                       r"|refix|rework|mix|dub|edit|made famous|in the style of)\b")
COMPILATION_WORDS = re.compile(
    r"\b(best of|greatest hits|the hits|hits collection|collection|anthology|anthologie"
    r"|essential|essentials|the very best|very best|integrale|ultimate|compilation"
    r"|platinum|definitive|the singles|singles collection|le meilleur|tubes"
    r"|retrospective|now that s what i call|\d+ (titres|tracks|succes|chansons))\b")
# Matching data, not prose: French and German words are kept on purpose so that
# French-language compilations are detected too.
VARIOUS_ARTISTS = {"various artists", "various", "artistes divers", "artistes varies",
                   "multi interpretes", "verschiedene interpreten", "compilation"}
SEEDS_PER_CALL = 5        # playlist tracks analyzed per engine call
SUGGEST_CALLS = 8         # number of engine calls per playlist
SIMILAR_ARTISTS = 15      # playlist artists whose related artists are always looked up
SIMILAR_PER_ARTIST = 4    # new related artists kept for each
TOP_TRACKS = 5            # most-played tracks checked for each suggested artist


def log(*args):
    print(datetime.now(PARIS).strftime("%H:%M:%S"), *args, flush=True)


def load_config():
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
    return config


def flat(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def norm(text):
    """Title or artist without accents, parentheses or remaster notes."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    text = re.sub(r"\s-\s[^-]*(remaster|version|edit|mono|stereo)[^-]*$", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def artist_of(track):
    return (track.get("performer") or (track.get("album") or {}).get("artist") or {})


def song_key(track):
    """Same song, whatever the version or album."""
    return norm(artist_of(track).get("name")), norm(track.get("title"))


def genre_of(track):
    return ((track.get("album") or {}).get("genre") or {}).get("name") or ""


def year_of(track):
    date = (track.get("album") or {}).get("release_date_original") or ""
    return int(date[:4]) if date[:4].isdigit() else None


def items_of(data, *keys):
    """List of items in a service response, whatever its shape."""
    if isinstance(data, list):
        return data
    for key in keys:
        part = data.get(key)
        if isinstance(part, list):
            return part
        if isinstance(part, dict) and isinstance(part.get("items"), list):
            return part["items"]
    return data.get("items", []) if isinstance(data.get("items"), list) else []


class MusicService:
    def __init__(self, email, password):
        self.email, self.password = email, password
        self.token = None
        self.user_id = None
        self.headers = {"X-App-Id": APP_ID, "User-Agent": "Mozilla/5.0"}
        self.top_source = None   # "service" or "deezer" once known

    def login(self):
        try:
            r = requests.post(API + "/user/login", headers=self.headers, timeout=20,
                              params={"email": self.email, "password": self.password,
                                      "app_id": APP_ID})
            data = r.json()
        except Exception:
            raise RuntimeError("Could not reach the service (network error or unreadable response)") from None
        if "user_auth_token" not in data:
            raise RuntimeError(f"Service login refused (HTTP {r.status_code})")
        self.token = data["user_auth_token"]
        self.user_id = (data.get("user") or {}).get("id")

    def call(self, method, path, body=None, **params):
        for attempt in (1, 2):
            headers = {**self.headers, "X-User-Auth-Token": self.token}
            r = requests.request(method, API + path, params=params, json=body,
                                 headers=headers, timeout=30)
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
            items = items_of(data, "playlists")
            playlists.extend(items)
            if len(items) < 500:
                return playlists
            offset += 500

    def playlist_tracks(self, playlist_id):
        tracks, offset = [], 0
        while True:
            data = self.call("GET", "/playlist/get", playlist_id=playlist_id,
                             extra="tracks", limit=500, offset=offset)
            items = data.get("tracks", {}).get("items", [])
            tracks.extend(items)
            if len(items) < 500:
                return tracks
            offset += 500

    def create_playlist(self, name, description):
        data = self.call("POST", "/playlist/create", name=name, description=description[:200],
                         is_public="0", is_collaborative="0")
        if "id" not in data:
            raise RuntimeError("Playlist creation: unexpected response")
        return data["id"]

    def add_tracks(self, playlist_id, track_ids):
        for i in range(0, len(track_ids), 50):
            self.call("POST", "/playlist/addTracks", playlist_id=playlist_id,
                      track_ids=",".join(str(t) for t in track_ids[i:i + 50]),
                      no_duplicate="true")

    def suggest(self, seeds, listened):
        """The service's radio engine: tracks close to the 'seeds' tracks."""
        body = {"limit": 50,
                "listened_tracks_ids": listened[:200],
                "track_to_analysed": [{"track_id": t["id"],
                                       "artist_id": artist_of(t).get("id")} for t in seeds]}
        return items_of(self.call("POST", "/dynamic/suggest", body=body), "tracks")

    def similar_artists(self, artist_id):
        data = self.call("GET", "/artist/getSimilarArtists", artist_id=artist_id, limit=15)
        return items_of(data, "artists")

    def artist_top_tracks(self, artist):
        """An artist's most-played tracks, from most to least played.
        First the service ranking (artist/page); if refused, the Deezer one
        (public API), each track then being looked up in the service catalog."""
        if self.top_source != "deezer":
            try:
                return self.service_top(artist)
            except Exception as e:
                if self.top_source is None:
                    response = getattr(e, "response", None)
                    detail = (f"HTTP {response.status_code} {response.text[:150]}"
                              if response is not None else type(e).__name__)
                    log(f"   Service ranking refused ({detail}), using the Deezer ranking")
                    self.top_source = "deezer"
                else:
                    raise
        return self.deezer_top(artist)

    def service_top(self, artist):
        data = self.call("GET", "/artist/page", artist_id=artist.get("id"))
        tracks = []
        for item in (data.get("top_tracks") or [])[:TOP_TRACKS]:
            if isinstance(item, dict) and item.get("id"):
                try:
                    full = self.call("GET", "/track/get", track_id=item["id"])
                except Exception:
                    continue
                if full.get("id"):
                    tracks.append(full)
        self.top_source = "service"
        return tracks

    def deezer_top(self, artist):
        name = artist.get("name") or ""
        r = requests.get(DEEZER + "/search/artist", params={"q": name, "limit": 5}, timeout=20)
        r.raise_for_status()
        match = [a for a in r.json().get("data", []) if flat(a.get("name")) == flat(name)]
        if not match:
            return []
        r = requests.get(f"{DEEZER}/artist/{match[0]['id']}/top",
                         params={"limit": TOP_TRACKS + 3}, timeout=20)
        r.raise_for_status()
        tracks = []
        for hit in r.json().get("data", []):
            found = self.find_on_service(artist, hit.get("title_short") or hit.get("title"))
            if found is not None:
                tracks.append(found)
            if len(tracks) >= TOP_TRACKS:
                break
        return tracks

    def find_on_service(self, artist, title):
        """This artist's track on the service, original album version if possible."""
        query = " ".join(re.sub(r"\(.*?\)|\[.*?\]", " ",
                                f"{artist.get('name') or ''} {title or ''}").split())
        try:
            data = self.call("GET", "/catalog/search", query=query, limit=30)
        except Exception:
            return None
        wanted = norm(title)
        same = [t for t in items_of(data, "tracks")
                if norm(t.get("title")) == wanted
                and (artist_of(t).get("id") == artist.get("id")
                     or flat(artist_of(t).get("name")) == flat(artist.get("name")))]
        clean = [t for t in same if is_clean(t)]
        if clean:
            return min(clean, key=album_rank)
        return same[0] if same else None

    def clean_version(self, track):
        """Same song outside compilations, live or remix (original album preferred)."""
        name, title = artist_of(track).get("name") or "", track.get("title") or ""
        query = re.sub(r"\(.*?\)|\[.*?\]", " ", f"{name} {title}")
        try:
            data = self.call("GET", "/catalog/search", query=" ".join(query.split()), limit=30)
        except Exception:
            return None
        same = [t for t in items_of(data, "tracks")
                if song_key(t) == song_key(track) and is_clean(t)]
        return min(same, key=album_rank) if same else None


def album_rank(track):
    """Prefers the original album, then the oldest."""
    album = track.get("album") or {}
    kind = str(album.get("release_type") or album.get("product_type") or "").lower()
    single = kind in ("single", "epsingle") or 0 < (album.get("tracks_count") or 0) <= 3
    return (1 if single else 0, album.get("release_date_original") or "9999")


def is_clean(track):
    """Rules out live, remix, karaoke, compilations and unplayable tracks."""
    if track.get("streamable") is False or not track.get("id"):
        return False
    album = track.get("album") or {}
    raw = " ".join([track.get("title") or "", track.get("version") or "",
                    album.get("title") or ""]).lower()
    if BAD_WORDS.search(raw):
        return False
    album_artist = flat((album.get("artist") or {}).get("name"))
    if album_artist in VARIOUS_ARTISTS or COMPILATION_WORDS.search(flat(album.get("title"))):
        return False
    return True


def profile(tracks):
    artists = Counter()
    names = {}
    for t in tracks:
        a = artist_of(t)
        if a.get("id"):
            artists[a["id"]] += 1
            names[a["id"]] = a.get("name") or "?"
    genres = Counter(genre_of(t) for t in tracks if genre_of(t))
    years = sorted(y for y in (year_of(t) for t in tracks) if y)
    return artists, names, genres, years


def analyze(service, source, suggestions_id, count, max_per_artist=1):
    tracks = service.playlist_tracks(source["id"])
    if not tracks:
        log(f"   Playlist '{source['name']}' is empty, nothing to analyze")
        return []
    artists, names, genres, years = profile(tracks)
    top_genres = {g for g, _ in genres.most_common(3)}
    if years:
        low, high = years[len(years) // 10], years[(len(years) * 9) // 10]
        log(f"   {len(tracks)} tracks, {len(artists)} artists, era {low}-{high}")
    else:
        low = high = None
        log(f"   {len(tracks)} tracks, {len(artists)} artists")
    log("   Genres: " + ", ".join(f"{g} ({n})" for g, n in genres.most_common(4)))
    log("   Top artists: " + ", ".join(
        f"{names[a]} ({n})" for a, n in artists.most_common(6)))

    known_ids = {t["id"] for t in tracks}
    known_songs = {song_key(t) for t in tracks}
    # Artists already suggested in an earlier run are skipped, so that each run
    # brings the biggest hits of new artists instead of lesser tracks of the same ones.
    already_ids, already_names = set(), set()
    if suggestions_id is not None:
        for t in service.playlist_tracks(suggestions_id):
            known_ids.add(t["id"])
            known_songs.add(song_key(t))
            a = artist_of(t)
            if a.get("id"):
                already_ids.add(a["id"])
            if a.get("name"):
                already_names.add(flat(a["name"]))
        if already_ids:
            log(f"   {len(already_ids)} artists already suggested earlier, skipped")

    # 1. Related artists: The service's radio engine, then artists similar
    #    to the playlist's artists. 'links' counts how many times each one comes up.
    links = Counter()
    via = {}
    candidates = {}

    def propose(artist, reason):
        """Records a related artist; returns True if it is a new one."""
        aid = artist.get("id")
        name = artist.get("name")
        if isinstance(name, dict):
            name = name.get("display")
        if (not aid or aid in artists or aid in already_ids
                or flat(name) in VARIOUS_ARTISTS or flat(name) in already_names):
            return False
        is_new = aid not in candidates
        links[aid] += 1
        candidates.setdefault(aid, {"id": aid, "name": name or "?"})
        via.setdefault(aid, reason)
        return is_new

    pool = tracks[:]
    random.shuffle(pool)
    for i in range(SUGGEST_CALLS):
        seeds = pool[i * SEEDS_PER_CALL:(i + 1) * SEEDS_PER_CALL]
        if not seeds:
            break
        try:
            results = service.suggest(seeds, [t["id"] for t in tracks])
        except Exception as e:
            log(f"   Suggestion engine unavailable ({type(e).__name__}), "
                "moving on to similar artists")
            break
        for t in results:
            propose(artist_of(t), "service engine")
    # The playlist's main artists first; then the others, until there are
    # enough new artists (earlier runs may have used up the closest ones).
    for n, (artist_id, _) in enumerate(artists.most_common()):
        if n >= SIMILAR_ARTISTS and len(candidates) >= count * 2:
            break
        try:
            similar = service.similar_artists(artist_id)
        except Exception as e:
            log(f"   Artists similar to {names[artist_id]}: failed ({type(e).__name__})")
            continue
        added = 0
        for other in similar:
            if added >= SIMILAR_PER_ARTIST:
                break
            if propose(other, f"close to {names[artist_id]}"):
                added += 1
    log(f"   {len(candidates)} new related artists found")

    # 2. For each one, their most-played tracks, in order of popularity
    hits = {}          # artist -> kept tracks, from most to least played
    top_ok = top_ko = 0
    for aid in sorted(candidates, key=lambda a: -links[a])[:count * 2]:
        try:
            top = service.artist_top_tracks(candidates[aid])
            top_ok += 1
        except Exception as e:
            top_ko += 1
            if top_ko == 1:
                log(f"   Most-played tracks of {candidates[aid]['name']}: failed "
                    f"({type(e).__name__} {str(e)[:120]})")
            continue
        kept, seen = [], set()
        for position, t in enumerate(top, 1):
            key = song_key(t)
            if t.get("id") in known_ids or key in known_songs or key in seen:
                continue
            if not is_clean(t):
                t = service.clean_version(t)
                if t is None or t.get("id") in known_ids:
                    continue
            seen.add(key)
            kept.append((t, position))
        if kept:
            hits[aid] = kept
    log(f"   Most-played tracks read for {top_ok} artists"
        + (f" ({top_ko} failures)" if top_ko else ""))

    # 3. Ranking: first each artist's most-played track, starting with the artists
    #    most linked to the playlist and of the same genre or era;
    #    then, if there is room left, their second track, and so on.
    def fit(aid):
        t = hits[aid][0][0]
        points = links[aid] * 3
        if genre_of(t) in top_genres:
            points += 2
        y = year_of(t)
        if low and y and low - 3 <= y <= high + 3:
            points += 1
        return points

    order = sorted(hits, key=lambda a: -fit(a))
    chosen, picked = [], set()
    rank = 0
    while len(chosen) < count and any(len(hits[a]) > rank for a in order) and rank < max_per_artist:
        for aid in order:
            if len(chosen) >= count:
                break
            if len(hits[aid]) > rank and song_key(hits[aid][rank][0]) not in picked:
                t, position = hits[aid][rank]
                picked.add(song_key(t))
                chosen.append((t, position, via[aid]))
        rank += 1
    for t, position, reason in chosen:
        version = f" ({t['version']})" if t.get("version") else ""
        log(f"   + {artist_of(t).get('name')} - {(t.get('title') or '').strip()}{version}"
            f" [{genre_of(t) or '?'}, {year_of(t) or '?'}] artist's #{position}, {reason}")
    return [t for t, _, _ in chosen]


def radio_names(config):
    """The radio's playlists, never analyzed in '*' mode."""
    fixed = {flat(config.get("main_playlist")), flat(config.get("previous_main_playlist"))}
    patterns = []
    for key in ("daily_playlist", "monthly_playlist"):
        p = config.get(key)
        if p:
            p = re.escape(flat(p.replace("{date}", "DATEX").replace("{month}", "MONTHX")))
            p = p.replace("datex", r"\d{4} \d{2} \d{2}").replace("monthx", r"\d{4} \d{2}")
            patterns.append(re.compile(rf"^{p}$"))
    return fixed - {""}, patterns


def main():
    config = load_config()
    email = os.environ.get("MUSIC_EMAIL", "").strip()
    password = os.environ.get("MUSIC_PASSWORD", "").strip()
    if not email or not password:
        raise SystemExit("MUSIC_EMAIL / MUSIC_PASSWORD secrets missing")
    if not API or not APP_ID:
        raise SystemExit("MUSIC_API_URL / MUSIC_APP_ID secrets missing")
    wanted = os.environ.get("PLAYLISTS", "").strip() or config.get("analyze_playlists", "")
    pattern = config.get("suggestions_name") or "Suggestions {playlist}"
    count = os.environ.get("COUNT", "").strip() or config.get("suggestions_count", "")
    count = int(count) if count.isdigit() and int(count) > 0 else 30
    per_artist = config.get("suggestions_per_artist", "")
    per_artist = int(per_artist) if per_artist.isdigit() and int(per_artist) > 0 else 1
    if not wanted:
        raise SystemExit("No playlist to analyze (config.txt: analyze_playlists)")

    service = MusicService(email, password)
    service.login()
    log("Service login OK")
    playlists = service.list_playlists()
    mine = [p for p in playlists if service.user_id is None
            or (p.get("owner") or {}).get("id") in (None, service.user_id)]
    by_name = {flat(p.get("name")): p for p in mine}
    suggestion_prefix = flat(pattern.replace("{playlist}", ""))

    if wanted.strip() == "*":
        fixed, patterns = radio_names(config)
        sources = [p for p in mine if flat(p.get("name")) not in fixed
                   and not any(r.match(flat(p.get("name"))) for r in patterns)
                   and not (suggestion_prefix and flat(p.get("name")).startswith(suggestion_prefix))]
    else:
        sources = []
        for name in (n.strip() for n in wanted.split(",")):
            if not name:
                continue
            p = by_name.get(flat(name))
            if p is None:
                log(f"Playlist '{name}' not found among your playlists")
            else:
                sources.append(p)
    if not sources:
        raise SystemExit("No playlist to analyze found")

    total = 0
    for source in sources:
        target_name = pattern.replace("{playlist}", source["name"].strip())
        log(f"Analyzing '{source['name']}' -> '{target_name}'")
        target = by_name.get(flat(target_name))
        target_id = target["id"] if target else None
        try:
            chosen = analyze(service, source, target_id, count, per_artist)
        except Exception as e:
            log(f"   Analysis failed ({type(e).__name__}): {str(e)[:150]}")
            continue
        if not chosen:
            log("   No new suggestion")
            continue
        if target_id is None:
            target_id = service.create_playlist(
                target_name, f"Tracks close to the playlist {source['name']}, to sort")
            by_name[flat(target_name)] = {"id": target_id, "name": target_name}
            log(f"   Playlist created: {target_name}")
        service.add_tracks(target_id, [t["id"] for t in chosen])
        total += len(chosen)
        log(f"   {len(chosen)} suggestions added to '{target_name}'")
    log(f"Done: {total} suggestions added")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        log(str(e))
        sys.exit(1)
