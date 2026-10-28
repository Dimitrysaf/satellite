"""Google Messages for web: what the embedded view may load, and its settings."""

import os
import tempfile
import unittest
from unittest import mock

from satellite_sms import google_messages
from satellite_sms.google_messages import navigation_policy
from satellite_sms.settings import MessagesSettings


class NavigationPolicyTests(unittest.TestCase):
    def check(self, uri, expected, main_frame=True, clicked=False):
        self.assertEqual(navigation_policy(uri, main_frame, clicked), expected, uri)

    def test_google_messages_loads_inside(self):
        self.check("https://messages.google.com/web/conversations/12", "allow")
        self.check("https://www.gstatic.com/messages/frame.html", "allow", main_frame=False)
        self.check("about:blank", "allow", main_frame=False)

    def test_clicked_links_open_in_the_browser(self):
        self.check("https://example.com/article", "external", clicked=True)
        self.check("https://accounts.google.com/settings", "external", clicked=True)
        self.check("tel:+306941234567", "external", clicked=True)

    def test_the_page_cannot_go_anywhere_by_itself(self):
        self.check("https://example.com/ad", "block")
        self.check("https://evil.example/frame", "block", main_frame=False)
        self.check("https://messages.google.com.evil.example/", "block")

    def test_dangerous_schemes_never_load(self):
        for uri in ("javascript:alert(1)", "file:///etc/passwd", "http://messages.google.com/web"):
            self.check(uri, "block")
        self.check("http://messages.google.com/web", "external", clicked=True)  # only via the browser


class LoadErrorTests(unittest.TestCase):
    def test_error_text_names_the_status(self):
        self.assertIn("error 403", google_messages.load_error_text(403))
        self.assertIn("Couldn't reach", google_messages.load_error_text(None))
        self.assertIn("Couldn't reach", google_messages.load_error_text(200))


class SettingsTests(unittest.TestCase):
    def test_saved_and_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "satellite", "messages.json")
            settings = MessagesSettings(path)
            self.assertFalse(settings.get("google_messages"))
            settings.set("google_messages", True)
            self.assertTrue(MessagesSettings(path).get("google_messages"))
            with self.assertRaises(ValueError):
                settings.set("google_messages", "yes")

    def test_delete_data_removes_both_folders(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as cache, \
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": data, "XDG_CACHE_HOME": cache}):
            for folder in google_messages.data_dirs():
                os.makedirs(folder)
                open(os.path.join(folder, "cookies.sqlite"), "w").close()
            google_messages.delete_data()
            self.assertFalse(any(os.path.exists(f) for f in google_messages.data_dirs()))


if __name__ == "__main__":
    unittest.main()
