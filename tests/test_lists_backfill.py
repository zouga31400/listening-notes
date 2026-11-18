"""Cover backfill of the older Notion playlist pages (python -m unittest discover tests)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import notion_lists as nl  # noqa: E402


def page(i, name, link, cover=None):
    return {"id": f"p{i}", "cover": cover, "properties": {
        "Name": {"type": "title", "title": [{"plain_text": name}]},
        "Link": {"type": "url", "url": link}}}


class FakeNotion:
    def __init__(self, pages):
        self.pages, self.patched = pages, []

    def call(self, method, path, body=None, params=None):
        if method == "PATCH":
            self.patched.append((path, body))
        return {}

    def paginate(self, method, path, body=None):
        return iter(self.pages)


class FakeService:
    def __init__(self):
        self.asked = []

    def playlist_tracks(self, playlist_id):
        self.asked.append(playlist_id)
        if playlist_id == "2":
            return [{"id": 1, "album": {}}]
        return [{"id": 1, "album": {}}, {"id": 2, "album": {"image": {"large": "https://img/a.jpg"}}}]


class Backfill(unittest.TestCase):
    def run_backfill(self, pages):
        notion = FakeNotion(pages)
        db = mock.Mock(status_type="select", id="db", notion=notion)
        db.title_of = lambda p: p["properties"]["Name"]["title"][0]["plain_text"]
        service = FakeService()
        env = {"NOTION_TOKEN": "t", "NOTION_DATABASE_ID": "ea8e66660faa4ce299e2f599916f1058"}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(nl, "config_value", return_value=""), \
             mock.patch.object(nl, "Database", return_value=db), \
             mock.patch.object(nl, "Notion", return_value=notion), \
             mock.patch.object(nl.lq, "connect", return_value=service):
            code = nl.backfill_covers()
        return code, notion, service

    def test_pages_without_cover_get_the_first_album_cover_and_icon(self):
        code, notion, service = self.run_backfill([
            page(1, "Rock", "https://play.example/playlist/1"),
            page(2, "No art", "https://play.example/playlist/2"),
            page(3, "No link", ""),
            page(4, "Has cover", "https://play.example/playlist/4", cover={"type": "external"})])
        self.assertEqual(code, 0)
        self.assertEqual(service.asked, ["1", "2"])     # no cover to find for 2; 3 and 4 skipped
        self.assertEqual([p for p, _ in notion.patched], ["/pages/p1"])
        body = notion.patched[0][1]
        self.assertEqual(body["cover"]["external"]["url"], "https://img/a.jpg")
        self.assertEqual(body["icon"]["external"]["url"], "https://img/a.jpg")

    def test_nothing_to_do_does_not_log_in(self):
        code, notion, service = self.run_backfill(
            [page(1, "Done", "https://x/playlist/1", cover={"a": 1})])
        self.assertEqual((code, notion.patched, service.asked), (0, [], []))


if __name__ == "__main__":
    unittest.main()
