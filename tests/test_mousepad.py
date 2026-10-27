"""Tests for the KDE Connect mousepad special key table."""

import unittest

from satellite.plugins.mousepad import SPECIAL_KEYS


class SpecialKeyTests(unittest.TestCase):
    def test_matches_kde_connect_numbering(self):
        expected = {
            1: "BackSpace", 2: "Tab", 4: "Left", 5: "Up", 6: "Right",
            7: "Down", 8: "Page_Up", 9: "Page_Down", 10: "Home", 11: "End",
            12: "Return", 13: "Delete", 14: "Escape", 21: "F1", 32: "F12",
        }
        for number, name in expected.items():
            self.assertEqual(SPECIAL_KEYS[number], name)

    def test_zero_is_invalid(self):
        self.assertNotIn(0, SPECIAL_KEYS)

    def test_function_keys_are_contiguous(self):
        self.assertEqual(
            [SPECIAL_KEYS[n] for n in range(21, 33)],
            ["F{}".format(i) for i in range(1, 13)],
        )


if __name__ == "__main__":
    unittest.main()
