"""Let the paired device change this computer's volume (KDE Connect's systemvolume).

Uses ``pactl``, which talks to PulseAudio and to PipeWire's pulse server.
The phone sees every output (sink) and can change its volume, mute it or make
it the default; changes made on the desktop are pushed back to the phone.
"""

import json
import subprocess
import threading
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

NORMAL_VOLUME = 65536  # PulseAudio's 100%


def run_pactl(*arguments):
    try:
        result = subprocess.run(
            ["pactl", *arguments], check=True, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


def parse_sinks(listing, default):
    """Turn ``pactl -f json list sinks`` output into the protocol's sinkList."""
    try:
        sinks = json.loads(listing or "[]")
    except ValueError:
        return []
    result = []
    for sink in sinks if isinstance(sinks, list) else ():
        channels = [c.get("value", 0) for c in (sink.get("volume") or {}).values() if isinstance(c, dict)]
        result.append({
            "name": sink.get("name", ""),
            "description": sink.get("description") or sink.get("name", ""),
            "muted": bool(sink.get("mute")),
            "volume": max(channels) if channels else 0,
            "maxVolume": NORMAL_VOLUME,
            "enabled": sink.get("name") == default,
        })
    return result


class Plugin:
    plugin_id = "systemvolume"
    display_name = _("Control volume on this computer")
    description = _("Change this computer's volume and audio output from the paired device.")
    icon = "audio-volume-high-symbolic"
    incoming_capabilities = ("kdeconnect.systemvolume.request",)
    outgoing_capabilities = ("kdeconnect.systemvolume",)

    def __init__(self, pactl=run_pactl, watcher=None):
        self._pactl = pactl
        self._watcher = watcher if watcher is not None else self._watch
        self._daemon = None
        self._last = {}
        self._watching = False
        self._lock = threading.Lock()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.systemvolume.request" in remote_outgoing

    def sinks(self):
        default = (self._pactl("get-default-sink") or "").strip()
        return parse_sinks(self._pactl("-f", "json", "list", "sinks"), default)

    def on_connected(self, daemon, device_id):
        self._daemon = daemon
        with self._lock:
            start, self._watching = not self._watching, True
        if start and self._watcher:
            threading.Thread(target=self._watcher, daemon=True).start()

    def handle_packet(self, daemon, link, packet):
        if packet.get("type") != "kdeconnect.systemvolume.request":
            return False
        body = packet.get("body", {})
        if body.get("requestSinks"):
            self._send_sink_list(daemon, link["id"])
        name = body.get("name")
        if isinstance(name, str) and name:
            volume = body.get("volume")
            if isinstance(volume, (int, float)) and not isinstance(volume, bool):
                self._pactl("set-sink-volume", name, str(max(0, int(volume))))
            if isinstance(body.get("muted"), bool):
                self._pactl("set-sink-mute", name, "1" if body["muted"] else "0")
            if body.get("enabled") is True:
                self._pactl("set-default-sink", name)
        return True

    def _send_sink_list(self, daemon, device_id):
        sinks = self.sinks()
        with self._lock:
            self._last = {sink["name"]: sink for sink in sinks}
        daemon.send_plugin_packet(self.plugin_id, device_id, "kdeconnect.systemvolume", {"sinkList": sinks})

    def push_changes(self):
        """Send what changed since the last look to every connected device."""
        daemon = self._daemon
        if daemon is None:
            return
        sinks = {sink["name"]: sink for sink in self.sinks()}
        with self._lock:
            last, self._last = self._last, sinks
        devices = [d["id"] for d in daemon.devices()
                   if d.get("connected") and daemon.plugins.is_enabled_for(d["id"], self.plugin_id)]
        if set(sinks) != set(last):
            for device_id in devices:  # an output appeared or vanished
                daemon.send_plugin_packet(
                    self.plugin_id, device_id, "kdeconnect.systemvolume", {"sinkList": list(sinks.values())})
            return
        for name, sink in sinks.items():
            changed = {key: sink[key] for key in ("volume", "muted", "enabled") if sink[key] != last[name][key]}
            if changed:
                for device_id in devices:
                    daemon.send_plugin_packet(
                        self.plugin_id, device_id, "kdeconnect.systemvolume", dict(changed, name=name))

    def _watch(self):
        """Follow ``pactl subscribe`` and push sink and server changes."""
        try:
            process = subprocess.Popen(
                ["pactl", "subscribe"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            return
        for line in process.stdout:
            if ("sink" in line and "sink-input" not in line) or "server" in line:
                try:
                    self.push_changes()
                except Exception as error:  # keep following events
                    print("Volume update failed: {!r}".format(error), flush=True)
