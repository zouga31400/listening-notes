"""Soundtrack album check for film lists (python -m unittest discover tests).

A film list is replaced by an existing soundtrack album only when the album has every
requested song and the songs are at least half of it (not mostly the score)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lists_sync as lq  # noqa: E402

lq.SEARCH_PAUSE = 0


def track(i, title, performer):
    return {"id": i, "title": title, "performer": {"name": performer}, "streamable": True}


SONGS = [track(1, "Song A", "Ann"), track(2, "Song B", "Bob"), track(3, "Song C", "Cid")]
SCORE = [track(100 + i, f"Cue {i}", "Composer") for i in range(10)]
ENTRIES = [(1, "Ann", "Song A"), (2, "Bob", "Song B"), (3, "Cid", "Song C")]


class FakeService:
    def __init__(self, album_tracks, album_title="Film X (Original Motion Picture Soundtrack)"):
        self.album_tracks, self.album_title, self.created = album_tracks, album_title, None

    def call(self, method, path, **params):
        if path == "/catalog/search":
            return {"albums": {"items": [{"id": "A1", "title": self.album_title},
                                         {"id": "Z", "title": "Unrelated Hits"}]}}
        if path == "/album/get":
            return {"tracks": {"items": self.album_tracks}, "artist": {"name": "Various"}}
        return {}

    def search(self, query):
        return [t for t in SONGS if t["title"].lower() in query.lower()]

    def find_playlist(self, name):
        return None

    def create_playlist(self, name, description=None):
        self.created, self.description = name, description
        return 7

    def playlist_tracks(self, playlist_id):
        return []

    def add_tracks(self, playlist_id, ids):
        return len(ids)


def run(album_tracks, film="Film X (2001)", **kw):
    service = FakeService(album_tracks, **kw)
    result = lq.build_playlist(service, "Film X songs", ENTRIES, [], "test", film=film)
    return service, result


class SoundtrackAlbum(unittest.TestCase):
    def test_album_with_every_song_and_little_score_replaces_the_playlist(self):
        service, result = run(SONGS + SCORE[:2])
        self.assertTrue(result["redirect"])
        self.assertIsNone(service.created)

    def test_album_with_every_song_but_mostly_score_still_gives_a_playlist(self):
        service, result = run(SONGS + SCORE)
        self.assertFalse(result["redirect"])
        self.assertEqual(service.created, "Film X songs")
        self.assertEqual((result["album"]["covered"], result["album"]["wanted"]), (3, 3))

    def test_album_missing_a_song_gives_a_playlist_and_the_album_link(self):
        service, result = run(SONGS[:2])
        self.assertFalse(result["redirect"])
        self.assertEqual(service.created, "Film X songs")
        self.assertEqual(result["album"]["missing"], ["Cid - Song C"])

    def test_album_with_few_of_the_songs_is_not_mentioned(self):
        _, result = run(SONGS[:1])
        self.assertIsNone(result["album"])

    def test_album_of_another_film_is_ignored(self):
        _, result = run(SONGS, album_title="Other Film (Original Motion Picture Soundtrack)")
        self.assertFalse(result["redirect"])
        self.assertIsNone(result["album"])

    def test_no_film_means_no_album_search(self):
        service = FakeService(SONGS)
        result = lq.build_playlist(service, "Rock", ENTRIES, [], "test")
        self.assertFalse(result["redirect"])
        self.assertEqual(service.created, "Rock")

    def test_film_line_in_a_file(self):
        lines, film = lq.split_film(["Titanic songs", "Film: Titanic (1997)",
                                     "Celine Dion - My Heart Will Go On"])
        self.assertEqual(film, "Titanic (1997)")
        self.assertEqual(lines, ["Titanic songs", "Celine Dion - My Heart Will Go On"])


class Description(unittest.TestCase):
    def test_default_author_comes_from_config(self):
        self.assertEqual(lq.description_for(), "Created by fsyl20")

    def test_author_named_in_the_request(self):
        self.assertEqual(lq.description_for("  Marie  "), "Created by Marie")

    def test_new_playlist_gets_the_author(self):
        service = FakeService([])
        lq.build_playlist(service, "Rock", ENTRIES, [], "test", author="Marie")
        self.assertEqual(service.description, "Created by Marie")

    def test_managed_playlists_are_recognized(self):
        for description in ("Created by fsyl20", "Created by Marie", "Created by lists_sync.py",
                            "Créée par liste_qobuz.py", "Created by list_qobuz.py"):
            self.assertTrue(lq.is_managed({"description": description}), description)
        for description in ("", "My favourite songs", "Created by me for the summer road trip "
                            "with all the friends from school and their families too"):
            self.assertFalse(lq.is_managed({"description": description}), description)

    def test_existing_managed_playlist_gets_the_new_description(self):
        class Existing(FakeService):
            updated = None

            def find_playlist(self, name):
                return {"id": 9, "description": "Created by lists_sync.py"}

            def set_description(self, playlist_id, description):
                self.updated = (playlist_id, description)

            def remove_tracks(self, playlist_id, ids):
                pass

        service = Existing([])
        lq.build_playlist(service, "Rock", ENTRIES, [], "test")
        self.assertEqual(service.updated, (9, "Created by fsyl20"))

    def test_author_line_in_a_file(self):
        lines, meta = lq.split_meta(["Road trip", "Author: Marie", "Film: Cars (2006)",
                                     "Sheryl Crow - Real Gone"])
        self.assertEqual(meta, {"film": "Cars (2006)", "author": "Marie"})
        self.assertEqual(lines, ["Road trip", "Sheryl Crow - Real Gone"])


if __name__ == "__main__":
    unittest.main()
