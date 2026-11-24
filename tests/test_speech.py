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
