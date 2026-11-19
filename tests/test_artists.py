import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MUSIC_API_URL", "https://x")
import artists_sync as a  # noqa: E402


class ArtistsSync(unittest.TestCase):
    def test_strip_html_and_copyright(self):
        text = a.strip_html("<p>Ce groupe californien.</p><p>Linda Perry &amp; co.</p><p>©Copyright Music Story 2023</p>")
        bio, source = a.split_copyright(text)
        self.assertEqual(bio, "Ce groupe californien.\n\nLinda Perry & co.")
        self.assertEqual(source, "©Copyright Music Story 2023")

    def test_no_copyright_line(self):
        self.assertEqual(a.split_copyright("Just a bio."), ("Just a bio.", ""))

    def test_chunks_cut_on_spaces_within_the_limit(self):
        parts = a.chunks("mot " * 1200, size=1900)
        self.assertTrue(all(len(p) <= 1900 for p in parts))
        self.assertEqual(" ".join(parts).split(), ("mot " * 1200).split())

    def test_album_lines_keep_albums_once_oldest_first(self):
        items = [
            {"title": "Second", "release_date_original": "1995-05-01", "tracks_count": 10, "artist": {"id": 7}},
            {"title": "First", "released_at": 694224000, "tracks_count": 12, "artist": {"id": 7}},
            {"title": "First (Deluxe)", "release_date_original": "2003-01-01", "tracks_count": 20, "artist": {"id": 7}},
            {"title": "A Single", "release_date_original": "1993-01-01", "tracks_count": 2, "artist": {"id": 7}},
            {"title": "Tribute by others", "release_date_original": "2000-01-01", "tracks_count": 10, "artist": {"id": 99}},
        ]
        self.assertEqual(a.album_lines(items, 7), ["1992 · First", "1995 · Second"])


if __name__ == "__main__":
    unittest.main()
