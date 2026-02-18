import unittest

import numpy as np

from src.analysis.slot_analyzer import SlotAnalyzer


class BuffSimilarityTests(unittest.TestCase):
    def test_template_similarity_exact_match_is_high(self) -> None:
        arr = np.full((20, 20), 128, dtype=np.uint8)
        score = SlotAnalyzer._template_similarity(arr, arr.copy())
        self.assertGreaterEqual(score, 0.99)

    def test_template_similarity_unrelated_patterns_is_lower(self) -> None:
        roi = np.zeros((20, 20), dtype=np.uint8)
        roi[:, 10:] = 255
        tmpl = np.zeros((20, 20), dtype=np.uint8)
        tmpl[10:, :] = 255
        score = SlotAnalyzer._template_similarity(roi, tmpl)
        self.assertLess(score, 0.6)


if __name__ == "__main__":
    unittest.main()
