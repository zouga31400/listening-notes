"""Always-on listening outside the show (python -m unittest discover tests)."""
import asyncio
import os
import sys
import types
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.modules.setdefault("shazamio", types.SimpleNamespace(Shazam=None))
import radio_listen as rl  # noqa: E402
import radio_offshow as ro  # noqa: E402

PARIS = rl.PARIS
CONFIG = {"main_playlist": "Radio Shazam", "monthly_playlist": "Radio {month}",
          "daily_playlist": "Radio {date}", "decade_playlist": "Radio {decade}",
          "mood_period_playlist": "Radio {mood} {period}", "listen_start": "21:00",
          "latest_end": "02:00", "offshow_main_playlist": "Radio flux hors émission",
          "offshow_monthly_playlist": "Radio flux hors émission {month}",
          "offshow_decade_playlist": "Radio flux {decade}",
          "offshow_mood_period_playlist": "Radio flux {mood} {period}",
          "mood_names": "Calme, Groove, Énergique"}


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=PARIS)


class Plan(unittest.TestCase):
    def test_weekday_daytime_listens_until_the_show(self):
        mode, until = ro.plan(at("2026-11-18 20:30"), CONFIG)   # a Wednesday
        self.assertEqual((mode, until), ("listen", at("2026-11-18 21:00")))

    def test_daytime_listens_by_the_hour(self):
        self.assertEqual(ro.plan(at("2026-11-18 10:15"), CONFIG), ("listen", at("2026-11-18 11:15")))

    def test_show_time_pauses_while_the_show_runs_on_github(self):
        self.assertEqual(ro.plan(at("2026-11-18 21:05"), CONFIG), ("pause", at("2026-11-19 02:00")))

    def test_show_time_runs_the_show_here_when_asked(self):
        self.assertEqual(ro.plan(at("2026-11-18 21:05"), dict(CONFIG, offshow_show_here="yes")),
                         ("show", None))

    def test_after_midnight_listens(self):
        self.assertEqual(ro.plan(at("2026-11-19 00:45"), CONFIG)[0], "listen")

    def test_weekend_evening_listens(self):
        self.assertEqual(ro.plan(at("2026-11-21 21:30"), CONFIG)[0], "listen")   # a Saturday


class Config(unittest.TestCase):
    def test_offshow_names_replace_the_show_names_and_no_daily_playlist(self):
        off = ro.offshow_config(CONFIG)
        self.assertEqual(off["main_playlist"], "Radio flux hors émission")
        self.assertEqual(off["monthly_playlist"], "Radio flux hors émission {month}")
        self.assertEqual(rl.category_playlists(off, {"year": 1984, "mood": "Groove"}),
                         ["Radio flux 80s", "Radio flux Groove 60-80s"])
        self.assertNotIn("Radio {date}", off.values())

    def test_category_left_empty_is_off(self):
        off = ro.offshow_config(dict(CONFIG, offshow_decade_playlist=""))
        self.assertEqual(rl.category_playlists(off, {"year": 1984}), [])


class Service:
    def __init__(self):
        self.playlists, self.added, self.next_id = {}, [], 100

    def list_playlists(self):
        return [{"name": n, "id": i} for n, i in self.playlists.items()]

    def create_playlist(self, name, description):
        self.next_id += 1
        self.playlists[name] = self.next_id
        return self.next_id

    def playlist_track_ids(self, playlist_id):
        return set()

    def call(self, method, path, **params):
        if path == "/playlist/addTracks":
            name = next(n for n, i in self.playlists.items() if i == params["playlist_id"])
            self.added.append((name, params["track_ids"]))
        return {}


def item(key="a", when="2026-11-18 10:00"):
    return {"key": (key, key), "artist": "Artist " + key, "title": "Title " + key,
            "track": {"url": ""}, "match": {"id": key, "album": {"title": "Album"}, "duration": 200,
                                            "performer": {"name": "Artist " + key}},
            "sample_start": at(when), "out": {}}


class Keep(unittest.TestCase):
    def setUp(self):
        self.saved = (rl.track_info, rl.artist_country, rl.preview_features)
        rl.track_info = lambda match, track: {"year": 1984}
        rl.artist_country = lambda name: ""
        rl.preview_features = lambda *a, **k: None
        self.service = Service()
        self.off = ro.OffShow(self.service, CONFIG)

    def tearDown(self):
        rl.track_info, rl.artist_country, rl.preview_features = self.saved

    def test_track_goes_to_the_offshow_playlists_only(self):
        self.off.keep(item())
        month = f"{datetime.now(PARIS):%Y-%m}"
        self.assertEqual([n for n, _ in self.service.added],
                         ["Radio flux hors émission", f"Radio flux hors émission {month}",
                          "Radio flux 80s"])

    def test_same_track_heard_again_soon_is_one_play(self):
        self.assertFalse(self.off.is_replay(("a", "a"), at("2026-11-18 10:00")))
        self.assertTrue(self.off.is_replay(("a", "a"), at("2026-11-18 10:05")))
        self.assertFalse(self.off.is_replay(("a", "a"), at("2026-11-18 11:00")))


class Listen(unittest.TestCase):
    """A track is kept after two matching recognitions, once."""

    def test_confirmation_then_no_duplicate(self):
        service = Service()
        off = ro.OffShow(service, CONFIG)
        kept = []
        off.keep = lambda it: (kept.append(it["key"]), off.recent.__setitem__(it["key"],
                                                                             it["sample_start"]))
        song = {"track": {"subtitle": "Queen", "title": "Radio Ga Ga", "url": ""},
                "matches": [{"offset": 10}]}
        results = iter([song, song, song, {}])

        class Shazam:
            async def recognize(self, path):
                return next(results, {})

        saved = (rl.capture_any, rl.find_match, rl.compute_wait, ro.asyncio.sleep)
        rl.capture_any = lambda path: "main"
        rl.find_match = lambda *a: {"id": 1, "duration": 300}
        rl.compute_wait = lambda *a: 0
        calls = {"n": 0}

        async def no_sleep(seconds):
            calls["n"] += 1

        ro.asyncio.sleep = no_sleep
        try:
            until = datetime.now(PARIS) + timedelta(seconds=1)
            asyncio.run(off.listen(until, Shazam(), confirmations=2))
        finally:
            rl.capture_any, rl.find_match, rl.compute_wait, ro.asyncio.sleep = saved
        self.assertEqual(kept, [("queen", "radio ga ga")])


if __name__ == "__main__":
    unittest.main()
