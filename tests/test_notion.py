import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("STREAM_URL", "http://example.invalid/stream")
import radio_listen as r  # noqa: E402


class Response:
    def __init__(self, data=None, ok=True, status=200):
        self._data, self.ok, self.status_code = data or {}, ok, status

    def json(self):
        return self._data

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


class LinksAndCovers(unittest.TestCase):
    def test_cover_prefers_service_then_recognition(self):
        match = {"album": {"image": {"large": "https://img/large.jpg", "small": "s"}}}
        track = {"images": {"coverarthq": "https://shazam/hq.jpg"}}
        self.assertEqual(r.cover_url(match, track), "https://img/large.jpg")
        self.assertEqual(r.cover_url(None, track), "https://shazam/hq.jpg")
        self.assertEqual(r.cover_url(None, {}), "")

    def test_track_link_needs_secret(self):
        with mock.patch.dict(os.environ, {"MUSIC_WEB_URL": "https://web.example/"}):
            self.assertEqual(r.track_link({"id": 42}), "https://web.example/track/42")
            self.assertEqual(r.track_link(None), "")
        with mock.patch.dict(os.environ, {"MUSIC_WEB_URL": ""}):
            self.assertEqual(r.track_link({"id": 42}), "")

    def test_youtube_link(self):
        self.assertEqual(r.youtube_link("AC/DC", "T.N.T."),
                         "https://www.youtube.com/results?search_query=AC%2FDC+T.N.T.")


class NotionWrites(unittest.TestCase):
    def setUp(self):
        self.db = r.Notion("token", "abc")

    def test_check_adds_missing_properties_only(self):
        sent = {}
        with mock.patch.object(r.requests, "get", return_value=Response(
                {"properties": {"Title": {}, "Decade": {}}})), \
             mock.patch.object(r.requests, "patch",
                               side_effect=lambda url, **k: sent.update(k["json"]) or Response()):
            self.db.check(r.HISTORY_PROPERTIES)
        self.assertEqual(set(sent["properties"]), {"Link", "YouTube", "Cover", "Start time", "Cover URL",
                                                         "Album link", "Track", "Day playlist",
                                                         "Month playlist"})
        self.assertIn("Link", self.db.properties)

    def test_check_survives_refused_schema_change(self):
        with mock.patch.object(r.requests, "get", return_value=Response(
                {"properties": {"Title": {}}})), \
             mock.patch.object(r.requests, "patch", return_value=Response(ok=False, status=403)):
            self.db.check(r.HISTORY_PROPERTIES)
        self.assertEqual(self.db.properties, {"Title"})

    def test_history_page_has_cover_decade_links(self):
        self.db.properties = {"Title", "Artist", "Played at", "Status", "Year", "Decade",
                              "Link", "YouTube", "Cover"}
        bodies = []
        with mock.patch.object(r.requests, "post",
                               side_effect=lambda url, **k: bodies.append(k["json"]) or Response()):
            self.db.add_history("Artist", "Song", datetime(2026, 11, 11, 21, 0), "Added",
                                "Album", 200, "https://shazam/x", {"year": 1983, "genre": "Rock"},
                                cover="https://img/c.jpg", link="https://web/track/1")
        body = bodies[0]
        props = body["properties"]
        self.assertEqual(props["Decade"]["select"]["name"], "80s")
        self.assertEqual(props["Link"]["url"], "https://web/track/1")
        self.assertIn("youtube.com", props["YouTube"]["url"])
        self.assertNotIn("Album", props)   # not a property of this database: left out
        self.assertNotIn("Genre", props)
        self.assertEqual(body["cover"]["external"]["url"], "https://img/c.jpg")
        self.assertNotIn("icon", body)
        self.assertEqual(props["Cover"]["files"][0]["external"]["url"], "https://img/c.jpg")

    def test_history_keeps_the_exact_start_and_cover_url(self):
        self.db.properties = {"Title", "Artist", "Played at", "Status", "Start time", "Cover URL"}
        bodies = []
        with mock.patch.object(r.requests, "post",
                               side_effect=lambda url, **k: bodies.append(k["json"]) or Response()):
            self.db.add_history("Artist", "Song", datetime(2026, 11, 11, 21, 0, 37), "Added",
                                "", 0, "", {}, cover="https://img/c.jpg")
        props = bodies[0]["properties"]
        self.assertEqual(props["Start time"]["rich_text"][0]["text"]["content"],
                         "2026-11-11T21:00:37")
        self.assertEqual(props["Cover URL"]["url"], "https://img/c.jpg")

    def test_history_has_album_link_and_track_position(self):
        self.db.properties = {"Title", "Artist", "Played at", "Status", "Album link", "Track"}
        bodies = []
        with mock.patch.object(r.requests, "post",
                               side_effect=lambda url, **k: bodies.append(k["json"]) or Response()):
            self.db.add_history("Artist", "Song", datetime(2026, 11, 11, 21, 0), "Added",
                                "", 0, "", {}, album_url="https://web/album/9",
                                position="Piste 7/12")
        props = bodies[0]["properties"]
        self.assertEqual(props["Album link"]["url"], "https://web/album/9")
        self.assertEqual(props["Track"]["rich_text"][0]["text"]["content"], "Piste 7/12")

    def test_history_has_day_playlist_link(self):
        self.db.properties = {"Title", "Artist", "Played at", "Status", "Day playlist",
                              "Month playlist"}
        bodies = []
        with mock.patch.object(r.requests, "post",
                               side_effect=lambda url, **k: bodies.append(k["json"]) or Response()):
            self.db.add_history("Artist", "Song", datetime(2026, 11, 11, 21, 0), "Added",
                                "", 0, "", {}, day_playlist="https://web/playlist/5",
                                month_playlist="https://web/playlist/6")
        self.assertEqual(bodies[0]["properties"]["Day playlist"]["url"], "https://web/playlist/5")
        self.assertEqual(bodies[0]["properties"]["Month playlist"]["url"], "https://web/playlist/6")
        with mock.patch.dict(r.os.environ, {"MUSIC_WEB_URL": "https://web"}):
            self.assertEqual(r.playlist_link(5), "https://web/playlist/5")
            self.assertEqual(r.playlist_link(None), "")

    def test_backfill_evening_date(self):
        import notion_backfill as b
        self.assertEqual(b.evening_date("2026-11-17T00:54:00.000+01:00"), "2026-11-16")
        self.assertEqual(b.evening_date("2026-11-16T20:00:37.000Z"), "2026-11-16")

    def test_album_link_and_track_position(self):
        match = {"track_number": 7, "media_number": 1, "album": {"id": "abc123", "tracks_count": 12}}
        with mock.patch.dict(r.os.environ, {"MUSIC_WEB_URL": "https://web/"}):
            self.assertEqual(r.album_link(match), "https://web/album/abc123")
            self.assertEqual(r.album_link({}), "")
        self.assertEqual(r.track_position(match), "Piste 7/12")
        match["media_number"] = 2
        self.assertEqual(r.track_position(match), "CD2 · piste 7")
        self.assertEqual(r.track_position({"track_number": 3, "album": {}}), "Piste 3")
        self.assertEqual(r.track_position({}), "")
        self.assertEqual(r.track_position(None), "")

    def test_missing_page_has_youtube_and_details(self):
        self.db.properties = set(r.MISSING_PROPERTIES) | {"Title", "Artist", "Date", "Shazam"}
        bodies = []
        with mock.patch.object(r.requests, "post", side_effect=lambda url, **k: (
                bodies.append(k["json"]) or Response({"results": []}))):
            added = self.db.add_missing("Artist", "Song", "https://shazam/x",
                                        datetime(2026, 11, 11), {"year": 2004, "genre": "Pop"},
                                        "Some album", "https://img/c.jpg")
        self.assertTrue(added)
        props = bodies[-1]["properties"]
        self.assertEqual(props["Decade"]["select"]["name"], "2000s")
        self.assertEqual(props["Genre"]["rich_text"][0]["text"]["content"], "Pop")
        self.assertEqual(props["Shazam"]["url"], "https://shazam/x")
        self.assertIn("youtube.com", props["YouTube"]["url"])
        self.assertEqual(bodies[-1]["cover"]["external"]["url"], "https://img/c.jpg")


if __name__ == "__main__":
    unittest.main()
