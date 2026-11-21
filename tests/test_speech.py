import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import speech  # noqa: E402

PHRASE = "Vous venez d'écouter cette émission, merci de votre fidélité et à demain soir"


class PhraseHit(unittest.TestCase):
    def test_exact_and_partial(self):
        self.assertTrue(speech.phrase_hit(PHRASE, PHRASE))
        self.assertTrue(speech.phrase_hit("merci de votre fidélité et à demain", PHRASE))

    def test_tolerates_recognition_mistakes(self):
        self.assertTrue(speech.phrase_hit("vous venez d'écouter cette émissions merci", PHRASE))
        self.assertTrue(speech.phrase_hit("merci de vos fidélités et a demain soir", PHRASE))

    def test_other_speech_does_not_match(self):
        self.assertFalse(speech.phrase_hit("on enchaîne avec un titre de rock des années quatre-vingt", PHRASE))
        self.assertFalse(speech.phrase_hit("", PHRASE))
        self.assertFalse(speech.phrase_hit("merci à tous", ""))


if __name__ == "__main__":
    unittest.main()
