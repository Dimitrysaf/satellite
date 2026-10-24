"""Tests for the KDE Connect desktop runcommand service side."""

import json
import threading
import unittest

from satellite.plugins.runcommand import Plugin


class FakePluginManager:
    def __init__(self, commands):
        self.commands = commands

    def get_settings(self, _device_id, plugin_id):
        self.assert_runcommand(plugin_id)
        return {"commands": self.commands}

    @staticmethod
    def assert_runcommand(plugin_id):
        if plugin_id != "runcommand":
            raise AssertionError("unexpected plugin id")


class FakeDaemon:
    def __init__(self, commands):
        self.plugins = FakePluginManager(commands)
        self.sent = []
        self.finished = threading.Event()

    def _send_plugin_packet(self, device_id, packet_type, body):
        self.sent.append((device_id, packet_type, body))
        if body.get("commandFinished"):
            self.finished.set()
        return True


class RunCommandTests(unittest.TestCase):
    def test_builds_a_kde_connect_command_list(self):
        commands = Plugin.command_list(
            "Open editor | xed\ninvalid line\nTerminal | x-terminal-emulator"
        )
        self.assertEqual(len(commands), 2)
        self.assertEqual(
            {entry["name"] for entry in commands.values()},
            {"Open editor", "Terminal"},
        )
        self.assertEqual(
            commands,
            Plugin.command_list(
                "Open editor | xed\ninvalid line\nTerminal | x-terminal-emulator"
            ),
        )

    def test_request_for_command_list_sends_configured_entries(self):
        daemon = FakeDaemon("Terminal | x-terminal-emulator")
        plugin = Plugin()
        handled = plugin.handle_packet(
            daemon,
            {"id": "phone"},
            {"type": "kdeconnect.runcommand.request", "body": {"requestCommandList": True}},
        )
        self.assertTrue(handled)
        _, packet_type, body = daemon.sent[0]
        self.assertEqual(packet_type, "kdeconnect.runcommand")
        self.assertTrue(body["canAddCommand"])
        commands = json.loads(body["commandList"])
        self.assertEqual(next(iter(commands.values()))["command"], "x-terminal-emulator")

    def test_executes_only_a_configured_command_and_returns_output(self):
        daemon = FakeDaemon("Print text | printf 'runcommand-test'")
        plugin = Plugin()
        command_id = next(iter(Plugin.command_list(daemon.plugins.commands)))
        handled = plugin.handle_packet(
            daemon,
            {"id": "phone"},
            {"type": "kdeconnect.runcommand.request", "body": {"key": command_id}},
        )
        self.assertTrue(handled)
        self.assertTrue(daemon.finished.wait(timeout=3))
        output_packets = [entry[2] for entry in daemon.sent]
        self.assertTrue(any(packet.get("commandStarted") for packet in output_packets))
        self.assertTrue(any(packet.get("commandOutput") for packet in output_packets))
        self.assertTrue(any(packet.get("commandFinished") and packet["success"] for packet in output_packets))

    def test_unknown_command_key_does_not_execute(self):
        daemon = FakeDaemon("Print text | printf 'should-not-run'")
        plugin = Plugin()
        self.assertTrue(plugin.handle_packet(
            daemon,
            {"id": "phone"},
            {"type": "kdeconnect.runcommand.request", "body": {"key": "unknown"}},
        ))
        self.assertEqual(daemon.sent, [])


if __name__ == "__main__":
    unittest.main()
