"""Recognition of a recorded message that is identical every day (the announcement played after
the last title of the show), by comparing audio features: no audio is stored, only a small
spectral signature (16 bands, 10 frames per second) of a few samples.

How it learns: on each evening where the end of the show was found by another way, the samples
heard between the last title of the show and the music that follows are kept (their
signatures). The segments that two different evenings have in common are the message.
Afterwards, a sample that contains one of these segments means the show is over.
"""
import base64
import json
import subprocess

import numpy as np

SR = 8000
HOP = 800               # 10 frames per second
NFFT = 1024
BANDS = 16
EDGES = np.geomspace(100, 3800, BANDS + 1)
WIN = 60                # frames compared at once (6 s)
THRESHOLD = 0.85        # correlation (1 = identical) for two windows to be the same sound
MIN_RUN = 20            # consecutive matching windows: about 8 s in common at least
MAX_REFS = 6
FPS = SR / HOP


def decode(path):
    """Mono samples at 8 kHz from an audio file (needs ffmpeg)."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True, timeout=60).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0


def features(x):
    """(frames, BANDS) log energies of 16 bands between 100 Hz and 3.8 kHz."""
    if len(x) < NFFT:
        return np.zeros((0, BANDS), np.float32)
    n = 1 + (len(x) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(NFFT), axis=1)) ** 2
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    bands = np.stack([spec[:, (freqs >= lo) & (freqs < hi)].sum(axis=1)
                      for lo, hi in zip(EDGES[:-1], EDGES[1:])], axis=1)
    return np.log10(bands + 1e-7).astype(np.float32)


def file_features(path):
    return features(decode(path))


def pack(f):
    """Compact text of a signature (one byte per value)."""
    q = np.clip(np.round((f + 7.0) * 30), 0, 255).astype(np.uint8)
    return base64.b64encode(q.tobytes()).decode()


def unpack(text):
    q = np.frombuffer(base64.b64decode(text), dtype=np.uint8).astype(np.float32)
    return (q / 30.0 - 7.0).reshape(-1, BANDS)


def _windows(f):
    """Every window of WIN frames, mean removed per band and scaled to unit length."""
    n = len(f) - WIN + 1
    if n <= 0:
        return np.zeros((0, WIN * BANDS), np.float32)
    w = f[np.arange(WIN)[None, :] + np.arange(n)[:, None]]
    w = w - w.mean(axis=1, keepdims=True)
    w = w.reshape(n, -1)
    norm = np.linalg.norm(w, axis=1, keepdims=True)
    w = np.where(norm > 1e-3, w / np.maximum(norm, 1e-9), 0.0)    # silence matches nothing
    return w.astype(np.float32)


def _runs(good, min_run):
    out, start = [], None
    for i, g in enumerate(list(good) + [False]):
        if g and start is None:
            start = i
        elif not g and start is not None:
            if i - start >= min_run:
                out.append((start, i))
            start = None
    return out


def common(a, b):
    """Segments of `a` (start frame, end frame, start frame in b) that also sound in `b`."""
    wa, wb = _windows(a), _windows(b)
    if not len(wa) or not len(wb):
        return []
    sims = wa @ wb.T
    best, where = sims.max(axis=1), sims.argmax(axis=1)
    return [(s, e + WIN - 1, int(where[s])) for s, e in _runs(best >= THRESHOLD, MIN_RUN)]


def learn(days):
    """References from {day: [signatures]}: the segments that several days have in common."""
    refs = []
    names = sorted(days)
    for i, d1 in enumerate(names):
        for d2 in names[i + 1:]:
            for a in days[d1]:
                for b in days[d2]:
                    for s, e, _ in common(a, b):
                        seg = a[s:e]
                        if not any(common(seg, r) for r in refs):
                            refs.append(seg)
    return refs[:MAX_REFS]


def find(refs, sample):
    """(reference number, frame of the sample where the common sound starts, frame in the
    reference) when the sample contains a reference, else None."""
    for k, ref in enumerate(refs):
        hit = common(sample, ref)
        if hit:
            s, _, r = hit[0]
            return k, s, r
    return None


def dumps(samples):
    return json.dumps({"s": [pack(f) for f in samples]})


def loads(text):
    try:
        return [unpack(s) for s in json.loads(text).get("s", [])]
    except (ValueError, TypeError):
        return []
