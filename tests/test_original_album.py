"""The playlist sync prefers the original album over best-ofs and compilations
(python -m unittest discover tests)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lists_sync as lq  # noqa: E402

lq.SEARCH_PAUSE = 0


def track(i, album, album_artist="Glenn Frey", date="1984-06-01", tracks=10, kind="album"):
    return {"id": i, "title": "The Heat Is On", "performer": {"name": "Glenn Frey"}, "streamable": True,
            "album": {"title": album, "artist": {"name": album_artist}, "release_date_original": date,
                      "tracks_count": tracks, "release_type": kind}}


class Service:
    def __init__(self, results):
        self.results = results

    def search(self, query):
        return self.results


def pick(*results):
    return lq.best_match(Service(list(results)), "Glenn Frey", "The Heat Is On")["id"]


class OriginalAlbum(unittest.TestCase):
    def test_original_album_beats_a_best_of_listed_first(self):
        self.assertEqual(pick(track(1, "Greatest Hits", date="1995-01-01"),
                              track(2, "Beverly Hills Cop (Soundtrack)", album_artist="Various Artists"),
                              track(3, "The Allnighter")), 3)

    def test_various_artists_and_foreign_compilations_lose(self):
        for album, artist in (("Éxitos de los 80", "Glenn Frey"), ("Rock 2018", "Various Artists"),
                              ("Simply the Best", "Glenn Frey"), ("Le meilleur des années 80", "Glenn Frey"),
                              ("Now That's What I Call Music! 80s", "Glenn Frey")):
            self.assertEqual(pick(track(1, album, album_artist=artist), track(2, "The Allnighter")), 2, album)

    def test_box_set_loses(self):
        self.assertEqual(pick(track(1, "The Complete Recordings", tracks=80), track(2, "The Allnighter")), 2)

    def test_single_loses_to_the_album(self):
        self.assertEqual(pick(track(1, "The Heat Is On", tracks=2, kind="single"), track(2, "The Allnighter")), 2)

    def test_oldest_release_wins_a_tie(self):
        self.assertEqual(pick(track(1, "The Allnighter", date="2004-01-01"),
                              track(2, "The Allnighter", date="1984-06-01")), 2)

    def test_a_song_only_on_a_compilation_is_still_found(self):
        self.assertEqual(pick(track(1, "Beverly Hills Cop (Soundtrack)", album_artist="Various Artists")), 1)


if __name__ == "__main__":
    unittest.main()
