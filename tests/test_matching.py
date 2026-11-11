"""Regression tests for the track and album choice (python -m unittest discover tests).

Each case comes from a real mistake seen during the first evenings."""
import os
import sys
import types
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.modules.setdefault("shazamio", types.SimpleNamespace(Shazam=None))
import radio_listen as r  # noqa: E402


def track(artist, title, album, date_="2000-01-01", n=12, version=None, album_artist=None,
          isrc=None, kind=None):
    a = {"title": album, "release_date_original": date_, "tracks_count": n,
         "artist": {"name": album_artist or artist}}
    if kind:
        a["release_type"] = kind
    t = {"id": f"{album}|{title}|{version}", "title": title, "version": version,
         "performer": {"name": artist}, "album": a, "streamable": True}
    if isrc:
        t["isrc"] = isrc
    return t


def chosen(tracks, artist, title, isrc=None):
    m = r.pick(tracks, r.norm(artist), r.norm(title), isrc)
    return m and m["album"]["title"]


class Matching(unittest.TestCase):
    def test_artist_is_matched_on_whole_words(self):
        # "A - Nothing" once matched "Bruno Major - Nothing".
        self.assertIsNone(chosen([track("Bruno Major", "Nothing", "To Let A Good Thing Die")],
                                 "A", "Nothing"))
        self.assertEqual(chosen([track("A", "Nothing", "Hi-Fi Serious")], "A", "Nothing"),
                         "Hi-Fi Serious")

    def test_original_album_beats_best_of(self):
        tracks = [track("Dire Straits", "On Every Street",
                        "Sultans Of Swing - The Very Best Of Dire Straits", "1998-01-01", 16),
                  track("Dire Straits", "On Every Street", "On Every Street", "1991-09-09", 12)]
        self.assertEqual(chosen(tracks, "Dire Straits", "On Every Street"), "On Every Street")

    def test_refix_and_b_sides_lose(self):
        tracks = [track("Gorillaz", "Clint Eastwood", "G-Sides", "2001-03-12", 8,
                        version="Ed Case & Sweetie Irie Refix"),
                  track("Gorillaz", "Clint Eastwood", "Gorillaz", "2001-03-26", 15)]
        self.assertEqual(chosen(tracks, "Gorillaz", "Clint Eastwood"), "Gorillaz")

    def test_box_set_loses(self):
        tracks = [track("Pat Benatar", "Fire And Ice", "Synchronistic Wanderings", "1999-01-01",
                        48, version="Remastered"),
                  track("Pat Benatar", "Fire And Ice", "Precious Time", "1981-07-01", 9)]
        self.assertEqual(chosen(tracks, "Pat Benatar", "Fire and Ice"), "Precious Time")

    def test_remaster_beats_original(self):
        tracks = [track("Queen", "Bohemian Rhapsody", "A Night at the Opera", "1975-11-21"),
                  track("Queen", "Bohemian Rhapsody", "A Night at the Opera (2011 Remaster)",
                        "2011-03-14")]
        self.assertEqual(chosen(tracks, "Queen", "Bohemian Rhapsody"),
                         "A Night at the Opera (2011 Remaster)")

    def test_compilation_kept_when_nothing_else(self):
        self.assertEqual(chosen([track("Queen", "Bohemian Rhapsody", "Greatest Hits", n=17)],
                                "Queen", "Bohemian Rhapsody"), "Greatest Hits")

    def test_isrc_matches_despite_other_names(self):
        tracks = [track("The Artists", "Translated Song", "Orig", isrc="FR1234567890")]
        self.assertIsNone(chosen(tracks, "Les Artistes", "Chanson traduite"))
        self.assertEqual(chosen(tracks, "Les Artistes", "Chanson traduite", "FR1234567890"), "Orig")

    def test_search_title_drops_version_notes(self):
        self.assertEqual(r.search_title('On Every Street (7" Radio Edit)'), "On Every Street")
        self.assertEqual(r.search_title("Bohemian Rhapsody - Remastered 2011"), "Bohemian Rhapsody")


class TrackInfo(unittest.TestCase):
    def test_main_artist(self):
        self.assertEqual(r.main_artist("Don Broco & Sam Carter"), "Don Broco")
        self.assertEqual(r.main_artist("K.Flay feat. Tom Morello"), "K.Flay")
        self.assertEqual(r.main_artist("Earth, Wind & Fire"), "Earth, Wind")  # full name is tried first

    def test_country_tries_full_name_first(self):
        asked = []

        def fake(name):
            asked.append(name)
            return {"Earth, Wind & Fire": "United States", "Don Broco": "United Kingdom"}.get(name, "")

        original = r._lookup_country
        r._lookup_country = fake
        try:
            self.assertEqual(r.artist_country("Earth, Wind & Fire"), "United States")
            self.assertEqual(r.artist_country("Don Broco & Sam Carter"), "United Kingdom")
        finally:
            r._lookup_country = original
        self.assertEqual(asked, ["Earth, Wind & Fire", "Don Broco & Sam Carter", "Don Broco"])

    def test_info_from_service_then_recognition(self):
        match = {"duration": 245, "maximum_bit_depth": 24, "maximum_sampling_rate": 96.0,
                 "album": {"release_date_original": "1981-07-01", "genre": {"name": "Rock"},
                           "label": {"name": "Chrysalis"}}}
        info = r.track_info(match, {})
        info["country"] = "United States"
        self.assertEqual(r.info_line(info),
                         "1981 · Rock · United States · Chrysalis · 24-bit/96 kHz · 4:05")
        shazam = {"genres": {"primary": "Pop"}, "sections": [{"metadata": [
            {"title": "Label", "text": "Hungaroton"}, {"title": "Released", "text": "1983"}]}]}
        self.assertEqual(r.info_line(r.track_info(None, shazam)), "1983 · Pop · Hungaroton")


class ShowEnd(unittest.TestCase):
    def test_run_of_three_ends_the_show_and_keeps_the_first(self):
        d = r.EndDetector(3)
        self.assertEqual(d.on_track("A", True), ["A"])   # after speech: first of the run
        self.assertEqual(d.on_track("B", True), [])      # back to back: held
        self.assertEqual(d.on_track("C", True), [])      # third in a row: end
        self.assertTrue(d.ended)
        self.assertEqual(d.dropped, ["B", "C"])

    def test_speech_releases_held_tracks(self):
        d = r.EndDetector(3)
        d.on_track("A", True)
        self.assertEqual(d.on_track("B", True), [])
        self.assertEqual(d.on_speech(), ["B"])           # host speaks again: B was in the show
        self.assertEqual(d.on_track("C", True), ["C"])
        self.assertFalse(d.ended)

    def test_inactive_before_midnight(self):
        d = r.EndDetector(3)
        self.assertEqual([d.on_track(x, False) for x in "ABCD"], [["A"], ["B"], ["C"], ["D"]])
        self.assertFalse(d.ended)

    def test_run_started_before_midnight_counts(self):
        d = r.EndDetector(3)
        d.on_track("A", False)                            # 23:58, kept
        self.assertEqual(d.on_track("B", True), [])       # 00:02, held
        d.on_track("C", True)                             # 00:06, end
        self.assertTrue(d.ended)

    def test_held_tracks_kept_at_time_limit(self):
        d = r.EndDetector(4)
        d.on_track("A", True); d.on_track("B", True)
        self.assertEqual(d.finish(), ["B"])


class Housekeeping(unittest.TestCase):
    def test_only_old_daily_playlists_are_deleted(self):
        playlists = [{"name": "Radio 2026-09-01", "id": 1}, {"name": "Radio 2026-10-11", "id": 2},
                     {"name": "Radio 2026-11", "id": 3}, {"name": "Radio Shazam", "id": 4},
                     {"name": "Moto", "id": 5}]
        old = r.old_daily_playlists(playlists, "Radio {date}", 30, date(2026, 11, 10))
        self.assertEqual([i for i, _ in old], [1])
        self.assertEqual(r.old_daily_playlists(playlists, "Radio {date}", 0, date(2026, 11, 10)), [])

    def test_ffmpeg_errors_never_echo_the_url(self):
        msg = r.describe_ffmpeg_error("https://secret.example/live: HTTP error 404 Not Found")
        self.assertEqual(msg, "stream server returned HTTP 404")
        self.assertNotIn("secret", r.describe_ffmpeg_error("https://secret.example/live: odd"))


if __name__ == "__main__":
    unittest.main()
