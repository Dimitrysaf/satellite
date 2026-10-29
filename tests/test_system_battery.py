"""The phone's battery shown to the system through a virtual HID device (/dev/uhid)."""

import struct
import unittest
from unittest import mock

from satellite import system_battery
from satellite.plugins.battery import Plugin as Battery
from tests.fakes import FakeDaemon, LINK, packet


class UhidEventTests(unittest.TestCase):
    def test_create_event_matches_the_kernel_struct(self):
        event = system_battery.create_event("Galaxy A55 5G", "satellite-abc")
        self.assertEqual(len(event), system_battery.EVENT_SIZE)
        self.assertEqual(struct.unpack_from("<I", event)[0], system_battery.UHID_CREATE2)
        name, phys, uniq, rd_size, bus = struct.unpack_from("<128s64s64sHH", event, 4)
        self.assertEqual(name.rstrip(b"\0"), b"Galaxy A55 5G")
        self.assertEqual(uniq.rstrip(b"\0"), b"satellite-abc")
        self.assertEqual((rd_size, bus), (len(system_battery.REPORT_DESCRIPTOR), system_battery.BUS_VIRTUAL))

    def test_report_puts_the_charge_after_the_report_id(self):
        # The kernel reads the charge from byte 1 when it queries the battery.
        self.assertEqual(system_battery.report(87, True), bytes([1, 87, 1]))
        self.assertEqual(system_battery.report(140, False), bytes([1, 100, 0]))

    def test_battery_name_matches_the_udev_rule(self):
        # The kernel names it hid-<uniq>-battery; the rule matches hid-satellite-*-battery.
        self.assertEqual(system_battery.uniq_for("a1b2-c3_d4"), "satellite-a1b2c3d4")


class BatteryPluginTests(unittest.TestCase):
    def test_phone_battery_goes_to_the_system_and_away_on_disconnect(self):
        system = mock.Mock()
        plugin = Battery(reader=lambda: None, system=system)
        daemon = FakeDaemon(devices={LINK["id"]: {"id": LINK["id"], "name": LINK["name"]}})
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.battery", {"currentCharge": 64, "isCharging": True}))
        system.update.assert_called_once_with(LINK["id"], LINK["name"], 64, True)
        plugin.on_disconnected(daemon, LINK["id"])
        system.remove.assert_called_once_with(LINK["id"])

    def test_can_be_switched_off(self):
        system = mock.Mock()
        plugin = Battery(reader=lambda: None, system=system)
        daemon = FakeDaemon(settings={"battery": {"system": False}})
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.battery", {"currentCharge": 64}))
        system.update.assert_not_called()

    def test_this_computers_battery_is_sent_to_the_phone(self):
        daemon = mock.Mock()
        daemon._send_plugin_packet.return_value = True
        self.assertTrue(Battery.send(daemon, "d", "kdeconnect.battery", {"currentCharge": 50}))
        daemon._send_plugin_packet.assert_called_once_with("d", "kdeconnect.battery", {"currentCharge": 50})


if __name__ == "__main__":
    unittest.main()
