"""Run explicitly configured desktop commands from a paired device."""

import json
import os
import signal
import subprocess
import threading
import uuid

from satellite.plugins._base import PacketStatePlugin


ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True
MAX_COMMANDS = 50
MAX_COMMAND_LENGTH = 1024
MAX_OUTPUT_BYTES = 64 * 1024
RECOMMENDED_COMMANDS = (
    (
        "Restart now",
        "systemctl reboot",
    ),
    (
        "Shut down",
        "systemctl poweroff",
    ),
    (
        "Log out (Cinnamon)",
        "cinnamon-session-quit --logout --no-prompt",
    ),
    (
        "Sleep",
        "systemctl suspend",
    ),
    (
        "Wake another device (Wake-on-LAN)",
        "wakeonlan AA:BB:CC:DD:EE:FF",
    ),
    (
        "Restart to BIOS/UEFI",
        "systemctl reboot --firmware-setup",
    ),
    (
        "Lock screen",
        "loginctl lock-session",
    ),
)


class Plugin(PacketStatePlugin):
    plugin_id = "runcommand"
    display_name = "Run commands"
    description = "Configure desktop commands that this paired device may run."
    icon = "utilities-terminal-symbolic"
    incoming_capabilities = (
        "kdeconnect.runcommand",
        "kdeconnect.runcommand.output",
        "kdeconnect.runcommand.request",
    )
    outgoing_capabilities = (
        "kdeconnect.runcommand",
        "kdeconnect.runcommand.output",
        "kdeconnect.runcommand.request",
    )
    packet_types = ("kdeconnect.runcommand", "kdeconnect.runcommand.output")
    settings_schema = ({
        "key": "commands",
        "label": "Commands (one per line: Name | shell command)",
        "type": "multiline",
        "default": "",
    },)

    def __init__(self):
        self._processes = {}
        self._process_lock = threading.Lock()
        self._next_process_id = 0

    @staticmethod
    def configured_commands(value):
        """Read the current JSON format and older Name | command settings."""
        try:
            entries = json.loads(value)
        except (TypeError, ValueError):
            entries = None

        commands = []
        if isinstance(entries, list):
            for entry in entries[:MAX_COMMANDS]:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                command = entry.get("command")
                if (
                    isinstance(name, str) and name.strip()
                    and isinstance(command, str) and command.strip()
                    and len(name) <= 80 and len(command) <= MAX_COMMAND_LENGTH
                ):
                    commands.append((
                        name.strip(), command.strip(), bool(entry.get("custom"))
                    ))
            return commands

        commands = {}
        recommended = {
            (name, command)
            for name, command in RECOMMENDED_COMMANDS
        }
        for line in str(value or "").splitlines():
            if len(commands) >= MAX_COMMANDS or "|" not in line:
                continue
            name, command = (part.strip() for part in line.split("|", 1))
            if not name or not command:
                continue
            if len(name) > 80 or len(command) > MAX_COMMAND_LENGTH:
                continue
            is_custom = name == command or (name, command) not in recommended
            commands[(name, command)] = (name, command, is_custom)
        return list(commands.values())

    @classmethod
    def command_list(cls, value):
        """Build KDE Connect's serialized ID-to-command map."""
        commands = {}
        for name, command, _custom in cls.configured_commands(value):
            key = str(uuid.uuid5(uuid.NAMESPACE_URL, name + "\n" + command))
            commands[key] = {"name": name, "command": command}
        return commands

    @staticmethod
    def serialize_commands(commands):
        return json.dumps([
            {"name": name, "command": command, "custom": custom}
            for name, command, custom in commands
        ], separators=(",", ":"))

    @classmethod
    def is_supported(cls, remote_incoming, remote_outgoing):
        return (
            "kdeconnect.runcommand.request" in remote_outgoing
            or "kdeconnect.runcommand" in remote_incoming
        )

    def on_connected(self, daemon, device_id):
        self._send_command_list(daemon, device_id)

    def settings_changed(self, daemon, device_id):
        self._send_command_list(daemon, device_id)

    def _send_command_list(self, daemon, device_id):
        settings = daemon.plugins.get_settings(device_id, self.plugin_id)
        commands = self.command_list(settings.get("commands", ""))
        return daemon._send_plugin_packet(
            device_id,
            "kdeconnect.runcommand",
            {
                "commandList": json.dumps(commands, separators=(",", ":")),
                "canAddCommand": True,
            },
        )

    def handle_packet(self, daemon, link, packet):
        packet_type = packet.get("type")
        body = packet.get("body", {})

        if packet_type == "kdeconnect.runcommand.request":
            if body.get("requestCommandList") is True:
                self._send_command_list(daemon, link["id"])
                return True
            if body.get("stop") is True:
                self._stop_device_commands(link["id"])
                return True
            key = body.get("key")
            if isinstance(key, str):
                settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
                commands = self.command_list(settings.get("commands", ""))
                command = commands.get(key)
                if command:
                    self._start_command(daemon, link["id"], command)
            return True

        return super().handle_packet(daemon, link, packet)

    def _start_command(self, daemon, device_id, command):
        with self._process_lock:
            active_count = sum(len(items) for items in self._processes.values())
            if active_count >= MAX_COMMANDS:
                return False
            process_id = self._next_process_id
            self._next_process_id += 1

        try:
            process = subprocess.Popen(
                ["/bin/sh", "-c", command["command"]],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
            )
        except (OSError, subprocess.SubprocessError):
            self._send_output(daemon, device_id, {
                "commandFinished": True,
                "success": False,
                "exitCode": 127,
                "id": process_id,
            })
            return False

        with self._process_lock:
            self._processes.setdefault(device_id, {})[process_id] = process
        self._send_output(daemon, device_id, {
            "commandStarted": True,
            "command": command["name"],
            "id": process_id,
        })
        worker = threading.Thread(
            target=self._wait_for_command,
            args=(daemon, device_id, process_id, process),
            name="satellite-runcommand-{}".format(process_id),
            daemon=True,
        )
        worker.start()
        return True

    def _wait_for_command(self, daemon, device_id, process_id, process):
        output = {}

        def drain(channel, stream):
            captured = bytearray()
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    remaining = MAX_OUTPUT_BYTES - len(captured)
                    if remaining > 0:
                        captured.extend(chunk[:remaining])
            except OSError:
                pass
            output[channel] = bytes(captured)

        readers = [
            threading.Thread(target=drain, args=(name, stream), daemon=True)
            for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
        ]
        for reader in readers:
            reader.start()
        process.wait()
        for reader in readers:
            reader.join()
        stdout = output.get("stdout", b"")
        stderr = output.get("stderr", b"")
        self._send_output_lines(daemon, device_id, process_id, stdout, stderr)
        exit_code = process.returncode if process.returncode is not None else 1
        self._send_output(daemon, device_id, {
            "commandFinished": True,
            "success": exit_code == 0,
            "exitCode": exit_code,
            "id": process_id,
        })
        with self._process_lock:
            device_processes = self._processes.get(device_id, {})
            device_processes.pop(process_id, None)
            if not device_processes:
                self._processes.pop(device_id, None)

    def _send_output_lines(self, daemon, device_id, process_id, stdout, stderr):
        channels = (
            ("stdout", stdout),
            ("stderr", stderr),
        )
        for channel, data in channels:
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            for offset in range(0, len(lines), 5):
                self._send_output(daemon, device_id, {
                    "commandOutput": True,
                    channel: lines[offset:offset + 5],
                    "id": process_id,
                })

    @staticmethod
    def _send_output(daemon, device_id, body):
        daemon._send_plugin_packet(
            device_id, "kdeconnect.runcommand.output", body
        )

    def _stop_device_commands(self, device_id):
        with self._process_lock:
            processes = list(self._processes.get(device_id, {}).values())
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass

    def send(self, daemon, device_id, packet_type=None, body=None, **_kwargs):
        if packet_type == "kdeconnect.runcommand.request":
            return daemon._send_plugin_packet(device_id, packet_type, body or {})
        return False
