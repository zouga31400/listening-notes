"""Speech to text (faster-whisper) for the samples heard after midnight that are not music:
the text is kept in Notion so that the announcement ending the show can be read, and an
`end_phrase` of config.txt ends the show when it is heard. Optional: without the package
nothing changes."""
import re
import unicodedata
from difflib import SequenceMatcher

_model = None


def available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def transcribe(path):
    """French text of an audio file, '' when nothing is said."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = _model.transcribe(path, language="fr", beam_size=1,
                                    condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


def words(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", text).split()


def phrase_hit(text, phrase, chunk=4, threshold=0.8):
    """True when the text contains a piece of the phrase: any `chunk` consecutive words of the
    phrase that looks like the same words in the text (tolerates recognition mistakes)."""
    t, p = words(text), words(phrase)
    if not t or not p:
        return False
    size = min(chunk, len(p))
    pieces = [" ".join(p[i:i + size]) for i in range(len(p) - size + 1)]
    for i in range(len(t) - size + 1):
        window = " ".join(t[i:i + size])
        if any(SequenceMatcher(None, window, piece).ratio() >= threshold for piece in pieces):
            return True
    return False
