"""Pause media players while a phone call is ringing or active."""

import threading

ENABLED_BY_DEFAULT = True


class MprisPlayers:
    """Minimal MPRIS client over the session bus (needs only Gio)."""

    PREFIX = "org.mpris.MediaPlayer2."
    PATH = "/org/mpris/MediaPlayer2"
    IFACE = "org.mpris.MediaPlayer2.Player"

    def _bus(self):
        from gi.repository import Gio
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def playing(self):
        from gi.repository import Gio, GLib
        try:
            bus = self._bus()
            reply = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "ListNames", None, GLib.VariantType.new("(as)"),
                Gio.DBusCallFlags.NONE, 2000, None,
            )
            found = []
            for name in reply.unpack()[0]:
                if not name.startswith(self.PREFIX):
                    continue
                status = bus.call_sync(
                    name, self.PATH, "org.freedesktop.DBus.Properties", "Get",
                    GLib.Variant("(ss)", (self.IFACE, "PlaybackStatus")),
                    GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 2000, None,
                ).unpack()[0]
                if status == "Playing":
                    found.append(name)
            return found
        except GLib.Error:
            return []

    def call(self, name, method):
        from gi.repository import Gio, GLib
        try:
            self._bus().call_sync(
                name, self.PATH, self.IFACE, method, None, None,
                Gio.DBusCallFlags.NONE, 2000, None,
            )
            return True
        except GLib.Error:
            return False


class Plugin:
    plugin_id = "pausemusic"
    display_name = "Pause music during calls"
    description = "Pause playing media when the paired phone rings, and resume afterwards."
    icon = "media-playback-pause-symbolic"
    incoming_capabilities = ("kdeconnect.telephony",)
    outgoing_capabilities = ()
    settings_schema = (
        {"key": "only_talking", "label": "Wait until the call is answered", "type": "bool", "default": False},
        {"key": "resume", "label": "Resume playback when the call ends", "type": "bool", "default": True},
    )

    def __init__(self, players=None):
        self._players = players or MprisPlayers()
        self._paused = {}
        self._lock = threading.Lock()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.telephony" in remote_outgoing

    def handle_packet(self, daemon, link, packet):
        if packet.get("type") != "kdeconnect.telephony":
            return False
        body = packet.get("body", {})
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
        event = body.get("event")
        ended = bool(body.get("isCancel")) or event == "missedCall"
        trigger = "talking" if settings.get("only_talking", False) else "ringing"
        if ended:
            self._resume(link["id"], settings)
        elif event == trigger or (event == "talking" and trigger == "ringing"):
            self._pause(link["id"])
        return False  # telephony notifications still need this packet

    def _pause(self, device_id):
        with self._lock:
            if device_id in self._paused:
                return
            names = [name for name in self._players.playing() if self._players.call(name, "Pause")]
            self._paused[device_id] = names

    def _resume(self, device_id, settings):
        with self._lock:
            names = self._paused.pop(device_id, [])
        if settings.get("resume", True):
            for name in names:
                self._players.call(name, "Play")
