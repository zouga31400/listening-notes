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


class Transcribe(unittest.TestCase):
    def test_audio_is_decoded_with_ffmpeg_and_given_as_an_array(self):
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg not installed")
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        seen = {}

        class Seg:
            def __init__(self, text):
                self.text = text

        class Fake:
            def transcribe(self, audio, **kwargs):
                seen["audio"], seen["kwargs"] = audio, kwargs
                return [Seg(" bonjour "), Seg("à tous")], None

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.mp3")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=2",
                            "-ac", "1", "-ar", "44100", path], check=True)
            speech._model = Fake()
            try:
                self.assertEqual(speech.transcribe(path), "bonjour à tous")
            finally:
                speech._model = None
        self.assertIsInstance(seen["audio"], np.ndarray)
        self.assertEqual(seen["audio"].dtype, np.float32)
        self.assertAlmostEqual(len(seen["audio"]) / 16000, 2.0, delta=0.2)
        self.assertEqual(seen["kwargs"]["language"], "fr")


if __name__ == "__main__":
    unittest.main()


class StartMessageFallback(unittest.TestCase):
    """The show waits for its recorded message, but not for ever."""

    def setUp(self):
        os.environ.setdefault("STREAM_URL", "http://example.invalid/stream")

    def test_speech_does_not_start_the_show_while_the_message_is_awaited(self):
        import radio_listen as r
        starter = r.StartDetector(active=True, wait_message=True)
        starter.on_speech()
        self.assertFalse(starter.accept({"title": "music before the show"}))
        starter.on_message()
        self.assertTrue(starter.accept({"title": "first track of the show"}))

    def test_giving_up_goes_back_to_the_first_speech(self):
        import radio_listen as r
        starter = r.StartDetector(active=True, wait_message=True)
        self.assertFalse(starter.accept({"title": "music"}))
        starter.give_up_waiting()
        starter.on_speech()
        self.assertTrue(starter.accept({"title": "first track after speech"}))
        self.assertEqual(len(starter.ignored), 1)


class PhraseMatching(unittest.TestCase):
    PHRASE = "il est temps de reprendre ses activités quotidiennes demain matin avec le sourire"

    def test_common_words_do_not_match(self):
        # four words of the phrase are common in everyday talk: this must not end the show
        self.assertFalse(speech.phrase_hit("bon il est temps de partir maintenant", self.PHRASE))
        self.assertFalse(speech.phrase_hit("tu reprendre ses affaires demain matin", self.PHRASE))

    def test_a_part_of_the_message_with_mistakes_matches(self):
        self.assertTrue(speech.phrase_hit("il est temps de reprendre ses activité quotidienne", self.PHRASE))
        self.assertTrue(speech.phrase_hit("de reprendre ses activités quotidiennes demain matin", self.PHRASE))
