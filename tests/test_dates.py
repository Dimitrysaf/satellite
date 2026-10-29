"""Contact dates (BDAY) shown in the locale's own format."""

import locale
import unittest

from satellite.vcard import format_date, parse_date


def using_locale(name):
    try:
        locale.setlocale(locale.LC_TIME, name)
        return True
    except locale.Error:
        return False


class DateTests(unittest.TestCase):
    def tearDown(self):
        locale.setlocale(locale.LC_TIME, "C")

    def test_vcard_date_forms(self):
        self.assertEqual(parse_date("1990-05-21"), (1990, 5, 21))
        self.assertEqual(parse_date("19900521"), (1990, 5, 21))
        self.assertEqual(parse_date("1990-05-21T00:00:00Z"), (1990, 5, 21))
        self.assertEqual(parse_date("--05-21"), (None, 5, 21))
        self.assertEqual(parse_date("--0521"), (None, 5, 21))
        self.assertEqual(parse_date("1604-05-21"), (None, 5, 21))  # "year unknown"
        self.assertIsNone(parse_date("1990-13-01"))
        self.assertIsNone(parse_date("soon"))

    def test_us_order(self):
        if not using_locale("en_US.UTF-8"):
            self.skipTest("en_US.UTF-8 isn't installed")
        self.assertEqual(format_date("1990-05-21"), "05/21/1990")
        self.assertEqual(format_date("--05-21"), "05/21")

    def test_day_first_order(self):
        if not using_locale("el_GR.UTF-8"):
            self.skipTest("el_GR.UTF-8 isn't installed")
        self.assertEqual(format_date("1990-05-21"), "21/05/1990")
        self.assertEqual(format_date("--05-21"), "21/05")
        self.assertEqual(format_date("--02-29"), "29/02")

    def test_unreadable_dates_are_shown_as_written(self):
        self.assertEqual(format_date("2001-02-31"), "2001-02-31")
        self.assertEqual(format_date("in spring"), "in spring")


if __name__ == "__main__":
    unittest.main()
