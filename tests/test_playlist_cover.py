"""Cover of a playlist without album art (python -m unittest discover tests)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import playlist_cover as pc  # noqa: E402

POSTER = "https://upload.wikimedia.org/titanic.png"


def fake_wiki(url, params=None):
    """Answers like Wikipedia for a few pages."""
    if params and params.get("list") == "search":
        q = params["srsearch"].lower()
        if "titanic" in q:
            return {"query": {"search": [{"title": "Titanic (1953 film)"}, {"title": "Titanic (1997 film)"}]}}
        if "miami vice" in q:
            return {"query": {"search": [{"title": "Miami Vice"}]}}
        if "madonna" in q:
            return {"query": {"search": [{"title": "Madonna"}]}}
        if "eurodance" in q:
            return {"query": {"search": [{"title": "Eurodance"}]}}
        return {"query": {"search": []}}
    page = url.rsplit("/", 1)[1]
    pages = {
        "Titanic_(1953_film)": {"title": "Titanic (1953 film)", "description": "1953 film by Jean Negulesco",
                                "extract": "Titanic is a 1953 film.", "thumbnail": {"source": "old.png"}},
        "Titanic_(1997_film)": {"title": "Titanic (1997 film)", "description": "1997 film by James Cameron",
                                "extract": "Titanic is a 1997 American film.", "thumbnail": {"source": POSTER}},
        "Miami_Vice": {"title": "Miami Vice", "description": "American television series",
                       "extract": "Miami Vice is a series that aired from 1984 to 1989.",
                       "thumbnail": {"source": "vice.png"}},
        "Madonna": {"title": "Madonna", "description": "American singer (born 1958)",
                    "extract": "Madonna Louise Ciccone is a singer.", "thumbnail": {"source": "madonna.jpg"}},
        "Eurodance": {"title": "Eurodance", "description": "Music genre", "extract": "Eurodance is a genre."},
    }
    return pages[page]


class Themes(unittest.TestCase):
    def test_genre_decade_and_neutral(self):
        cases = {"Eurodance 1993-1996": "electro", "Soul Classics 60s": "soul", "Hits 2000s": "2000s",
                 "Rock années 80": "rock", "Années 90 - R&B": "rnb", "Road trip été": "musique",
                 "Jazz du dimanche": "jazz", "Lounge": "lounge", "Playlist pop 2010": "pop"}
        for name, slug in cases.items():
            self.assertTrue(pc.theme_cover(name).endswith(f"/covers/{slug}.png"), name)

    def test_every_theme_cover_exists(self):
        folder = os.path.join(os.path.dirname(__file__), "..", "covers")
        slugs = set(pc.GENRES) | set(pc.DECADES.values()) | {"musique"}
        for slug in slugs:
            self.assertTrue(os.path.exists(os.path.join(folder, f"{slug}.png")), slug)

    def test_film_title_and_year(self):
        self.assertEqual(pc.screen_title("Miami Vice (série TV, 1984-1990)"), ("Miami Vice", "1984", True))
        self.assertEqual(pc.screen_title("Heat (1995)"), ("Heat", "1995", False))
        self.assertEqual(pc.screen_title("Titanic"), ("Titanic", "", False))


class Lookups(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(pc, "get_json", side_effect=fake_wiki)
        patch.start()
        self.addCleanup(patch.stop)

    def test_film_poster_of_the_right_year(self):
        self.assertEqual(pc.find_cover("Titanic - chansons", film="Titanic (1997)"), (POSTER, "film"))

    def test_series_poster(self):
        self.assertEqual(pc.find_cover("Miami Vice", film="Miami Vice (série TV, 1984-1990)"),
                         ("vice.png", "film"))

    def test_unknown_film_falls_back_to_the_theme(self):
        url, source = pc.find_cover("Soul du film", film="Zzzz (2001)")
        self.assertEqual(source, "theme")
        self.assertTrue(url.endswith("/covers/soul.png"))

    def test_artist_who_makes_up_the_list(self):
        artists = ["Madonna"] * 4 + ["Prince", "Cher"]
        self.assertEqual(pc.find_cover("Hits", artists=artists), ("madonna.jpg", "artist"))

    def test_service_artist_image_comes_first(self):
        service = mock.Mock()
        service.call.return_value = {"artists": {"items": [
            {"name": "Madonna", "image": {"large": "https://service/madonna.jpg"}}]}}
        artists = ["Madonna"] * 3
        self.assertEqual(pc.find_cover("Hits", artists=artists, service=service),
                         ("https://service/madonna.jpg", "artist"))

    def test_mixed_artists_use_the_theme(self):
        artists = ["A", "B", "C", "D", "A", "B"]
        self.assertEqual(pc.find_cover("Eurodance 1993", artists=artists)[1], "theme")

    def test_network_failure_never_raises(self):
        with mock.patch.object(pc, "get_json", side_effect=pc.requests.ConnectionError("down")):
            url, source = pc.find_cover("Jazz", film="Titanic (1997)", artists=["Madonna"] * 3)
        self.assertEqual(source, "theme")
        self.assertTrue(url.endswith("/covers/jazz.png"))


if __name__ == "__main__":
    unittest.main()
