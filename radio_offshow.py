"""Always-on listening, for a machine that runs all the time (a small cloud server).

Outside the show (daytime, weekends, the music before the show starts and after it ends),
the radio is listened to non-stop and the tracks go to their own playlists and their own
Notion history ("offshow_*" keys in config.txt). The show itself is unchanged:

- offshow_show_here = no: the show stays on GitHub Actions (radio.yml). This machine pauses
  from listen_start on weekdays until the "show ended" signal (ntfy) or latest_end.
- offshow_show_here = yes: this machine also runs the show, with the same code as
  radio.yml (radio_listen.main), then goes back to the off-show listening. The external
  call that starts radio.yml in the evening must then be turned off.

Notifications: none outside the show, except errors. Run: python radio_offshow.py
(a systemd service restarts it if it stops; see deploy/oracle/README.md)."""
import asyncio
import json
import os
import time
from datetime import datetime, timedelta

import requests

import radio_listen as rl
from radio_listen import PARIS, log, log_detail

OFFSHOW_HEALTHCHECK_URL = os.environ.get("OFFSHOW_HEALTHCHECK_URL", "").strip().rstrip("/")
CATEGORY_KEYS = ("decade_playlist", "genre_playlist", "genre_decade_playlist", "mood_playlist",
                 "mood_period_playlist")
REPLAY_MINUTES = 20        # the same track heard again within this time is the same play
OUTAGE_ALERT_SECONDS = 600  # a stream outage is notified after this long
ERRORS_BEFORE_PAUSE = 15
ERROR_PAUSE_SECONDS = 600
SHOW_RETRY_SECONDS = 600    # a failed show session is started again after this long
HEARTBEAT_SECONDS = 600     # health ping while the process is alive


def offshow_config(config):
    """The config with the off-show playlist names in place of the show's."""
    off = dict(config)
    off["main_playlist"] = config.get("offshow_main_playlist", "").strip()
    off["monthly_playlist"] = config.get("offshow_monthly_playlist", "").strip()
    off["daily_playlist"] = off["excerpts_playlist"] = ""     # no daily playlist off the show
    for key in CATEGORY_KEYS:
        off[key] = config.get("offshow_" + key, "").strip()
    return off


def interval(config, key, default):
    value = config.get(key, "")
    return int(value) if value.isdigit() and int(value) >= 30 else default


def plan(now, config):
    """What to do now: ("show", None), ("pause", until) or ("listen", until).
    The show window starts at listen_start on weekdays (as radio.yml)."""
    hour, minute = rl.parse_hhmm(config.get("listen_start"), (21, 0))
    show_start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    latest = now.replace(hour=rl.LATEST_START[0], minute=rl.LATEST_START[1], second=0,
                         microsecond=0)
    if now.weekday() < 5 and show_start <= now < latest:
        if config.get("offshow_show_here", "no").strip().lower() in ("yes", "oui", "1", "true"):
            return "show", None
        hour, minute = rl.parse_hhmm(config.get("latest_end"), (2, 0))
        end = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return "pause", end
    until = now + timedelta(hours=1)       # config reloaded and health ping every hour
    if now.weekday() < 5 and now < show_start:
        until = min(until, show_start)
    return "listen", until


class OffShow:
    """Off-show playlists and Notion history. keep() is also called by the show session
    for the tracks before its start and after its end."""

    def __init__(self, service, config):
        self.service = service
        self.config = offshow_config(config)
        self.existing = {p["name"].strip().lower(): p["id"] for p in service.list_playlists()}
        self.categories = rl.Categories(service, self.existing)
        self.targets = {}          # label -> {"name", "id", "present"}
        self.recent = {}           # track key -> time of the last play kept
        self.added = self.logged = 0
        self.history = None
        db = config.get("offshow_history_db", "").strip()
        if rl.NOTION_TOKEN and db:
            history = rl.Notion(rl.NOTION_TOKEN, db)
            try:
                history.check(rl.HISTORY_PROPERTIES)
                self.history = history
                log("Notion off-show history: database reachable")
            except Exception as e:
                log(f"Notion off-show history: unreachable ({type(e).__name__})")
        else:
            log("Notion off-show history: not configured (offshow_history_db missing)")

    def target(self, label, name, description):
        """The playlist (created if missing), cached by label; renewed when the name changes
        (e.g. a new month)."""
        cached = self.targets.get(label)
        if cached and cached["name"] == name:
            return cached
        playlist_id = self.existing.get(name.lower())
        if playlist_id is None:
            playlist_id = self.service.create_playlist(name, description)
            self.existing[name.lower()] = playlist_id
            present = set()
            log(f"Playlist created: {name}")
        else:
            present = self.service.playlist_track_ids(playlist_id)
        self.targets[label] = {"name": name, "id": playlist_id, "present": present}
        return self.targets[label]

    def playlists(self, now):
        found = []
        main = self.config.get("main_playlist")
        if main:
            found.append(self.target("main", main, "Tracks recognized outside the show"))
        monthly = self.config.get("monthly_playlist")
        if monthly:
            found.append(self.target("monthly", monthly.replace("{month}", f"{now:%Y-%m}"),
                                     f"Tracks recognized outside the show in {now:%m/%Y}"))
        return found

    def is_replay(self, key, when):
        """True if the same track was kept less than REPLAY_MINUTES ago (still playing)."""
        last = self.recent.get(key)
        self.recent = {k: t for k, t in self.recent.items() if when - t < timedelta(hours=2)}
        if last is not None and when - last < timedelta(minutes=REPLAY_MINUTES):
            return True
        self.recent[key] = when
        return False

    def keep(self, item):
        """Adds a confirmed track to the off-show playlists and history (no notification)."""
        artist, title, track, match = item["artist"], item["title"], item["track"], item["match"]
        if self.is_replay(item["key"], item["sample_start"]):
            return
        status, album_name, duration = "Not found", None, None
        if match is not None:
            done = False
            try:
                targets = self.playlists(datetime.now(PARIS))
            except Exception as e:
                log(f"   Off-show playlists unavailable ({type(e).__name__})")
                targets = []
            for t in targets:
                if match["id"] in t["present"]:
                    continue
                try:
                    self.service.call("POST", "/playlist/addTracks", playlist_id=t["id"],
                                      track_ids=str(match["id"]), no_duplicate="true")
                    t["present"].add(match["id"])
                    done = True
                except Exception as e:
                    log(f"   Could not add (off-show): {type(e).__name__}")
            self.added += done
            status = "Added" if done else "Already present"
            album_name = (match.get("album") or {}).get("title")
            duration = match.get("duration")
        log_detail(f"   Off-show, {status}: {artist} - {title}")
        info = rl.track_info(match, track)
        info["country"] = rl.artist_country((match or {}).get("performer", {}).get("name") or artist)
        try:
            feats = rl.preview_features((match or {}).get("isrc") or track.get("isrc"), artist, title,
                                        fallback="sample.mp3" if item.get("current") else None)
        except Exception as e:
            log(f"   Audio analysis failed ({type(e).__name__})")
            feats = None
        if feats:
            info.update(feats)
            info["mood"] = rl.mood_of(feats, self.config)
        if match is not None:
            for name in rl.category_playlists(self.config, info):
                try:
                    self.categories.add(name, match["id"])
                except Exception as e:
                    log(f"   Could not add to an off-show category playlist ({type(e).__name__})")
        if self.history:
            song_start, _ = rl.song_bounds(item["out"], None, item["sample_start"])
            try:
                self.history.add_history(artist, title, song_start or item["sample_start"], status,
                                         album_name or rl.shazam_meta(track, "Album"), duration,
                                         track.get("url"), info, rl.cover_url(match, track),
                                         rl.track_link(match))
                self.logged += 1
            except Exception as e:
                log(f"   Notion off-show history failure ({type(e).__name__})")

    async def listen(self, until, shazam, confirmations):
        """Listens non-stop until `until`. A track is kept after `confirmations` matching
        recognitions (or at once when it is about to end)."""
        pending = None
        durations = {}
        errors_in_row = misses = stream_errors = 0
        outage_start = 0.0
        alerted = False
        while datetime.now(PARIS) < until:
            started = time.time()
            matched, confirming, remaining = False, False, None
            try:
                sample_start = datetime.now(PARIS)
                rl.capture_any("sample.mp3")
                if stream_errors and alerted:
                    outage = int(time.time() - outage_start)
                    rl.notify("Off-show listening: stream back",
                              f"Stream back after {outage // 60} min.", tags=["white_check_mark"])
                stream_errors, alerted = 0, False
                sample_end = time.time()
                out = await asyncio.wait_for(shazam.recognize("sample.mp3"), timeout=40)
                track = out.get("track")
                if track:
                    matched = True
                    artist, title = track["subtitle"], track["title"]
                    key = (rl.norm(artist), rl.norm(title))
                    if pending and pending["key"] == key:
                        pending["seen"] += 1
                    elif key in self.recent and sample_start - self.recent[key] < timedelta(
                            minutes=REPLAY_MINUTES):
                        pending = None          # the track already kept is still playing
                    else:
                        isrc = (track.get("isrc") or "").strip().upper() or None
                        found = rl.find_match(self.service, artist, title, key, isrc)
                        if found is not None:
                            durations[key] = found.get("duration")
                        pending = {"key": key, "artist": artist, "title": title, "track": track,
                                   "match": found, "seen": 1, "sample_start": sample_start,
                                   "out": out}
                    if pending:
                        left = rl.song_remaining(out, durations.get(key), sample_end)
                        ending = left is not None and left < rl.CYCLE_SECONDS + 20
                        if pending["seen"] >= confirmations or ending:
                            pending["current"] = True
                            self.keep(pending)
                            pending = None
                        else:
                            confirming = True
                    if not confirming:
                        remaining = rl.song_remaining(out, durations.get(key), sample_end)
                errors_in_row = 0
                misses = 0 if matched else misses + 1
            except rl.StreamError as e:
                stream_errors += 1
                if stream_errors == 1:
                    outage_start = started
                    log(f"Stream unavailable ({e}), retrying every {rl.STREAM_RETRY_SECONDS} s")
                if not alerted and time.time() - outage_start >= OUTAGE_ALERT_SECONDS:
                    alerted = True
                    rl.notify("Off-show listening: stream unavailable",
                              f"{e}, for more than {OUTAGE_ALERT_SECONDS // 60} min. Still retrying.",
                              tags=["warning"], priority=4)
                await asyncio.sleep(max(1, rl.STREAM_RETRY_SECONDS - (time.time() - started)))
                continue
            except Exception as e:
                errors_in_row += 1
                misses += 1
                log(f"Error ({type(e).__name__}): {str(e)[:150]}")
                if errors_in_row >= ERRORS_BEFORE_PAUSE:
                    rl.notify("Off-show listening: errors",
                              f"{errors_in_row} errors in a row ({type(e).__name__}). "
                              f"Pausing {ERROR_PAUSE_SECONDS // 60} min, then retrying.",
                              tags=["rotating_light"], priority=4)
                    errors_in_row = 0
                    await asyncio.sleep(ERROR_PAUSE_SECONDS)
                    continue
            if confirming:
                wait = rl.CYCLE_SECONDS - (time.time() - started)
            else:
                wait = rl.compute_wait(started, matched, remaining, misses)
            wait = min(wait, (until - datetime.now(PARIS)).total_seconds())
            if wait > 0:
                await asyncio.sleep(wait)


def show_ended_since(since):
    """True if the "show ended" signal was sent through ntfy after `since` (unix time)."""
    target = rl.SHOW_END_TOPIC or rl.NTFY_TOPIC
    if not target:
        return False
    if target.startswith("http"):
        server, _, topic = target.rstrip("/").rpartition("/")
    else:
        server, topic = "https://ntfy.sh", target
    try:
        r = requests.get(f"{server}/{topic}/json", params={"poll": "1", "since": str(int(since))},
                         timeout=20)
        r.raise_for_status()
    except Exception as e:
        log(f"ntfy check failed ({type(e).__name__})")
        return False
    for line in r.text.splitlines():
        try:
            message = json.loads(line).get("message") or ""
        except ValueError:
            continue
        if message.startswith("show-ended"):
            return True
    return False


async def pause_for_show(until):
    """The show runs on GitHub: wait for its end signal, or until `until`."""
    since = time.time()
    log(f"Show time: off-show listening paused until the show ends (at the latest {until:%H:%M})")
    while datetime.now(PARIS) < until:
        await asyncio.sleep(60)
        if show_ended_since(since):
            log("Show end signal received")
            return
    log("No show end signal, resuming at the time limit")


def ping(kind=""):
    if not OFFSHOW_HEALTHCHECK_URL:
        return
    try:
        requests.get(f"{OFFSHOW_HEALTHCHECK_URL}/{kind}" if kind else OFFSHOW_HEALTHCHECK_URL,
                     timeout=10)
    except Exception as e:
        log(f"Healthcheck ping failed ({type(e).__name__})")


async def heartbeat():
    """Pings OFFSHOW_HEALTHCHECK_URL every 10 minutes, show time included, so that a
    stopped machine is reported by the health check service."""
    while True:
        ping()
        await asyncio.sleep(HEARTBEAT_SECONDS)


async def run_forever():
    rl.check_secrets()
    beat = asyncio.create_task(heartbeat())  # noqa: F841 (kept alive for the whole run)
    from shazamio import Shazam
    shazam = Shazam()
    service = rl.MusicService()
    service.login()
    log("Service login OK")
    offshow = None
    while True:
        config = rl.load_config()            # picks up config.txt changes (git pull)
        if offshow is None:
            offshow = OffShow(service, config)
        else:
            offshow.config = offshow_config(config)
        mode, until = plan(datetime.now(PARIS), config)
        if mode == "show":
            log("Show time: running the show session")
            failed = False
            try:
                await rl.main(offshow=offshow)
            except SystemExit as e:
                failed = e.code not in (0, None)
                if failed:
                    rl.notify("Radio listening: session failed", str(e.code),
                              tags=["rotating_light"], priority=5)
            except Exception as e:
                failed = True
                rl.ping("fail")
                rl.notify("Radio listening: session failed", f"{type(e).__name__}: {str(e)[:300]}",
                          tags=["rotating_light"], priority=5)
                log(f"Show session failed ({type(e).__name__})")
            if failed:
                # Retried while the show window lasts, without a notification every minute.
                await asyncio.sleep(SHOW_RETRY_SECONDS)
            continue
        if mode == "pause":
            await pause_for_show(until)
            continue
        rl.CYCLE_SECONDS = interval(config, "offshow_listen_interval", 60)
        rl.SPEECH_CYCLE_SECONDS = interval(config, "offshow_speech_interval", 120)
        value = config.get("confirmations", "2")
        confirmations = int(value) if value.isdigit() and int(value) >= 1 else 2
        log(f"Off-show listening until {until:%H:%M}, every {rl.CYCLE_SECONDS} s "
            f"({offshow.added} additions and {offshow.logged} plays logged so far)")
        await offshow.listen(until, shazam, confirmations)


if __name__ == "__main__":
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        ping("fail")
        rl.notify("Off-show listening: stopped", f"{type(e).__name__}: {str(e)[:300]}",
                  tags=["rotating_light"], priority=5)
        raise
