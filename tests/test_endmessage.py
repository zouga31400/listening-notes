import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import endmessage as em  # noqa: E402


def message(seconds=25, seed=1):
    """A structured sound (moving tones with a rhythm), the same every day."""
    t = np.arange(int(em.SR * seconds)) / em.SR
    rng = np.random.default_rng(seed)
    x = np.zeros_like(t)
    for _ in range(6):
        f0, f1 = rng.uniform(200, 3000, 2)
        x += np.sin(2 * np.pi * (f0 * t + (f1 - f0) * t * t / (2 * seconds)))
    return (x * (0.6 + 0.4 * np.sin(2 * np.pi * rng.uniform(1, 4) * t))).astype(np.float32) / 6


def music(seconds, seed):
    return (np.random.default_rng(seed).normal(0, 0.1, int(em.SR * seconds))).astype(np.float32)


def evening(seed, gain=1.0):
    """Music, the message, music again; the second evening is a bit quieter and noisier."""
    x = np.concatenate([music(20, seed), message(), music(20, seed + 100)]) * gain
    return x + np.random.default_rng(seed + 7).normal(0, 0.004, len(x)).astype(np.float32)


def cut(x, start, seconds=15):
    return em.features(x[int(start * em.SR):int((start + seconds) * em.SR)])


class EndMessage(unittest.TestCase):
    def test_pack_roundtrip(self):
        f = em.features(message(5))
        self.assertLess(float(np.abs(em.unpack(em.pack(f)) - np.clip(f, -7, 255 / 30 - 7)).max()), 0.02)

    def test_two_evenings_share_the_message_only(self):
        a, b = evening(1), evening(2, gain=0.5)
        days = {"d1": [cut(a, 25), cut(a, 10)], "d2": [cut(b, 30), cut(b, 12)]}
        refs = em.learn(days)
        self.assertTrue(refs)
        # a sample of a third evening that overlaps the message is recognized
        c = evening(3, gain=0.8)
        hit = em.find(refs, cut(c, 28))
        self.assertIsNotNone(hit)
        # music alone is not
        self.assertIsNone(em.find(refs, em.features(music(15, 99))))
        self.assertIsNone(em.find(refs, cut(c, 0)))

    def test_nothing_learned_from_unrelated_evenings(self):
        days = {"d1": [em.features(music(15, 1))], "d2": [em.features(music(15, 2))]}
        self.assertEqual(em.learn(days), [])

    def test_silence_matches_nothing(self):
        silent = em.features(np.zeros(em.SR * 15, np.float32))
        self.assertEqual(em.common(silent, silent), [])

    def test_dumps_loads(self):
        f = em.features(message(10))
        back = em.loads(em.dumps([f, f]))
        self.assertEqual(len(back), 2)
        self.assertEqual(back[0].shape, f.shape)
        self.assertEqual(em.loads("not json"), [])


if __name__ == "__main__":
    unittest.main()
