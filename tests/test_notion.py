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
        self.assertEqual(set(sent["properties"]), {"Link", "YouTube", "Cover"})
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
