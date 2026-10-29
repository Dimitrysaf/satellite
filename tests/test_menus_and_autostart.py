"""Device menu entries (⋮ menu and tray) and the login autostart switch."""

import os
import tempfile
import unittest
from unittest import mock

from satellite import autostart
from satellite.plugins._base import PluginAction
from satellite.ui.device_menu import device_entries


class FakeController:
    def __init__(self, actions=()):
        self.daemon = mock.Mock()
        self.daemon.plugins.actions_for.return_value = [("p", a) for a in actions]


def labels(entries):
    return [None if e is None else e.label for e in entries]


class DeviceMenuTests(unittest.TestCase):
    def test_connected_device(self):
        actions = [PluginAction("ping", "Ping", "", None),
                   PluginAction("send-files", "Send files…", "", None, opens_window=True)]
        entries = device_entries(FakeController(actions), {
            "id": "d", "connected": True, "secure": True, "paired": True,
            "available_plugins": [{"id": "ping"}]})
        self.assertEqual(labels(entries),
                         ["Send files…", "Ping", None, "Encryption info", None, "Unpair"])
        self.assertEqual([e.opens_window for e in entries if e], [True, False, True, True])

    def test_pairing_request_only_in_the_tray(self):
        device = {"id": "d", "secure": True, "pair_requested": True, "verification_code": "AB12"}
        tray = labels(device_entries(FakeController(), device))
        page = labels(device_entries(FakeController(), device, include_pairing=False))
        self.assertEqual(tray[:3], ["AB12", "Accept", "Reject"])
        self.assertNotIn("Accept", page)

    def test_not_paired_device(self):
        device = {"id": "d", "secure": True, "available_plugins": [{"id": "ping"}]}
        self.assertEqual(labels(device_entries(FakeController(), device, include_pairing=False)),
                         ["Encryption info"])
        self.assertEqual(labels(device_entries(FakeController(), device))[0], "Request pairing")

    def test_pair_requested_can_be_cancelled_from_the_tray(self):
        device = {"id": "d", "secure": True, "pair_pending": True}
        self.assertEqual(labels(device_entries(FakeController(), device))[0], "Cancel pairing")

    def test_offline_device(self):
        self.assertEqual(labels(device_entries(FakeController(), {"id": "d", "paired": True})),
                         ["Unpair"])


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.system = os.path.join(self.home.name, "xdg")
        os.makedirs(os.path.join(self.system, "autostart"))
        env = {"XDG_CONFIG_HOME": os.path.join(self.home.name, "config"), "XDG_CONFIG_DIRS": self.system}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.home.cleanup()

    def install_system_entry(self):
        with open(os.path.join(self.system, "autostart", autostart.FILE_NAME), "w") as stream:
            stream.write(autostart.ENTRY)

    def test_packaged_entry_is_on_by_default_and_can_be_turned_off(self):
        self.install_system_entry()
        self.assertTrue(autostart.is_enabled())
        autostart.set_enabled(False)
        self.assertFalse(autostart.is_enabled())
        autostart.set_enabled(True)
        self.assertTrue(autostart.is_enabled())
        self.assertFalse(os.path.exists(autostart._user_path()))  # override removed

    def test_without_packaged_entry_a_user_entry_is_written(self):
        self.assertFalse(autostart.is_enabled())
        autostart.set_enabled(True)
        self.assertTrue(autostart.is_enabled())
        with open(autostart._user_path()) as stream:
            self.assertIn("Exec=satellite --background", stream.read())


if __name__ == "__main__":
    unittest.main()
