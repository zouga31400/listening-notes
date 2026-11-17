"""Sorting rules read from Notion (sort_playlist.py): genre rules and settings."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sort_playlist as sp  # noqa: E402


def rule_page(genre, playlist="", points=None):
    return {"properties": {
        "Genre": {"type": "title", "title": [{"plain_text": genre}]},
        "Playlist": {"type": "rich_text", "rich_text": [{"plain_text": playlist}] if playlist else []},
        "Energy points": {"type": "number", "number": points}}}


def setting_page(name, value):
    return {"properties": {
        "Setting": {"type": "title", "title": [{"plain_text": name}]},
        "Value": {"type": "rich_text", "rich_text": [{"plain_text": value}]}}}


class GenreRules(unittest.TestCase):
    def test_matching_ignores_case_accents_and_uses_words(self):
        self.assertTrue(sp.genre_matches("rock", "Pop/Rock"))
        self.assertTrue(sp.genre_matches("Métal", "metal"))
        self.assertTrue(sp.genre_matches("Metal", "Heavy Metal"))
        self.assertFalse(sp.genre_matches("Rock", "Rocksteady"))
        self.assertFalse(sp.genre_matches("Rock", ""))

    def test_rules_from_rows(self):
        rules = sp.rules_from_rows([rule_page("Classique", "Musique classique"),
                                    rule_page("Metal", points=20), rule_page("Jazz")])
        self.assertEqual(rules[0], {"genre": "Classique", "playlist": "Musique classique", "points": 0})
        self.assertEqual(rules[1]["points"], 20)
        self.assertEqual(rules[2], {"genre": "Jazz", "playlist": "", "points": 0})

    def test_effect_playlist_and_points_add_up(self):
        rules = sp.rules_from_rows([rule_page("Classique", "Musique classique"),
                                    rule_page("Rock", points=10), rule_page("Pop", points=-4)])
        self.assertEqual(sp.genre_effect(rules, "Classique"), ("Musique classique", 0))
        self.assertEqual(sp.genre_effect(rules, "Pop/Rock"), ("", 6))
        self.assertEqual(sp.genre_effect(rules, "Jazz"), ("", 0))

    def test_settings_override_valid_values_only(self):
        found = sp.settings_from_rows([setting_page("Calm up to", "50"),
                                       setting_page("Energetic from", "abc"),
                                       setting_page("Copy name", "{playlist} {period} {mood}"),
                                       setting_page("Unknown", "1")])
        self.assertEqual(found, {"calm_max_energy": "50",
                                 "sort_playlist_name": "{playlist} {period} {mood}"})


if __name__ == "__main__":
    unittest.main()
