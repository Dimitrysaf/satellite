"""Let the paired device control this computer's media players.

The reverse of the ``mpris`` plugin: the phone lists the desktop's MPRIS
players (Spotify, VLC, Firefox, ...), sees what is playing and can play,
pause, skip, seek and change their volume, like KDE Connect's mpriscontrol.
"""

import os
import threading
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

PREFIX = "org.mpris.MediaPlayer2."
OWN_PREFIX = "org.mpris.MediaPlayer2.satellite"  # the phone's players, shown by Satellite
PATH = "/org/mpris/MediaPlayer2"
ROOT = "org.mpris.MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
ACTIONS = ("Play", "Pause", "PlayPause", "Next", "Previous", "Stop")
MAX_ART_SIZE = 5 * 1024 * 1024


class DesktopPlayers:
    """The session's MPRIS players, over Gio (no GTK needed)."""

    def __init__(self):
        self._bus = None
        self._names = {}  # display name -> bus name

    def bus(self):
        from gi.repository import Gio
        if self._bus is None:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return self._bus

    def _call(self, name, iface, method, args=None, reply=None):
        from gi.repository import Gio, GLib
        return self.bus().call_sync(
            name, PATH, iface, method, args,
            GLib.VariantType.new(reply) if reply else None,
            Gio.DBusCallFlags.NONE, 2000, None)

    def _properties(self, name, iface):
        from gi.repository import GLib
        return self._call(name, "org.freedesktop.DBus.Properties", "GetAll",
                          GLib.Variant("(s)", (iface,)), "(a{sv})").unpack()[0]

    def names(self):
        """Display name -> bus name, with duplicates numbered like KDE does."""
        from gi.repository import GLib
        try:
            listed = self.bus().call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "ListNames", None, GLib.VariantType.new("(as)"), 0, 2000, None).unpack()[0]
        except GLib.Error:
            return {}
        names = {}
        for bus_name in sorted(n for n in listed if n.startswith(PREFIX) and not n.startswith(OWN_PREFIX)):
            try:
                identity = self._properties(bus_name, ROOT).get("Identity") or bus_name[len(PREFIX):]
            except GLib.Error:
                continue
            display, number = identity, 2
            while display in names:
                display, number = "{} [{}]".format(identity, number), number + 1
            names[display] = bus_name
        self._names = names
        return names

    def bus_name(self, display):
        return self._names.get(display) or self.names().get(display)

    def state(self, bus_name):
        return self._properties(bus_name, PLAYER)

    def call(self, bus_name, method, *args):
        from gi.repository import GLib
        signature = {"Seek": "(x)", "SetPosition": "(ox)"}.get(method)
        self._call(bus_name, PLAYER, method, GLib.Variant(signature, args) if signature else None)

    def set_property(self, bus_name, prop, value):
        from gi.repository import GLib
        self._call(bus_name, "org.freedesktop.DBus.Properties", "Set",
                   GLib.Variant("(ssv)", (PLAYER, prop, value)))


def now_playing(display, props):
    """The kdeconnect.mpris body describing one player's state."""
    metadata = props.get("Metadata") or {}
    artists = metadata.get("xesam:artist") or []
    body = {
        "player": display,
        "title": str(metadata.get("xesam:title") or ""),
        "artist": ", ".join(str(a) for a in artists) if isinstance(artists, (list, tuple)) else str(artists),
        "album": str(metadata.get("xesam:album") or ""),
        "isPlaying": props.get("PlaybackStatus") == "Playing",
        "pos": int(props.get("Position") or 0) // 1000,
        "length": int(metadata.get("mpris:length") or 0) // 1000,
        "canPause": bool(props.get("CanPause", False)),
        "canPlay": bool(props.get("CanPlay", False)),
        "canGoNext": bool(props.get("CanGoNext", False)),
        "canGoPrevious": bool(props.get("CanGoPrevious", False)),
        "canSeek": bool(props.get("CanSeek", False)),
        "volume": round(float(props.get("Volume", 1.0)) * 100),
    }
    if props.get("LoopStatus") is not None:
        body["loopStatus"] = props["LoopStatus"]
    if props.get("Shuffle") is not None:
        body["shuffle"] = bool(props["Shuffle"])
    url = metadata.get("xesam:url")
    if url:
        body["url"] = str(url)
    art = metadata.get("mpris:artUrl")
    if art:
        body["albumArtUrl"] = str(art)
    return body


def art_file(url):
    """The local file behind an album art URL, or None (web URLs aren't sent, as in KDE)."""
    from urllib.parse import unquote, urlparse
    parts = urlparse(url or "")
    if parts.scheme != "file":
        return None
    path = unquote(parts.path)
    return path if os.path.isfile(path) and os.path.getsize(path) <= MAX_ART_SIZE else None


class Plugin:
    plugin_id = "mpriscontrol"
    display_name = _("Control media on this computer")
    description = _("Play, pause and skip this computer's music and videos from the paired device.")
    icon = "multimedia-player-symbolic"
    incoming_capabilities = ("kdeconnect.mpris.request",)
    outgoing_capabilities = ("kdeconnect.mpris",)
    settings_schema = (
        {"key": "album_art", "label": _("Send album art to the device"), "type": "bool", "default": True},
    )

    def __init__(self, players=None):
        self._players = players or DesktopPlayers()
        self._daemon = None
        self._watching = False
        self._lock = threading.Lock()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.mpris.request" in remote_outgoing

    def on_connected(self, daemon, device_id):
        self._daemon = daemon
        self._send_player_list(daemon, device_id)
        with self._lock:
            start, self._watching = not self._watching, True
        if start:
            try:
                from gi.repository import GLib
                GLib.idle_add(self._watch)  # signals need the GTK main loop
            except ImportError:
                pass

    def handle_packet(self, daemon, link, packet):
        if packet.get("type") != "kdeconnect.mpris.request":
            return False
        body = packet.get("body", {})
        if body.get("requestPlayerList"):
            self._send_player_list(daemon, link["id"])
        display = body.get("player")
        bus_name = self._players.bus_name(display) if isinstance(display, str) else None
        if bus_name is None:
            return bool(body.get("requestPlayerList"))
        try:
            self._control(bus_name, body)
            if body.get("requestNowPlaying") or body.get("requestVolume"):
                self._send_state(daemon, link["id"], display, bus_name)
            if isinstance(body.get("albumArtUrl"), str):
                self._send_art(daemon, link["id"], display, bus_name, body["albumArtUrl"])
        except Exception as error:  # a player that vanished or refused
            print("Media control of {} failed: {!r}".format(display, error), flush=True)
        return True

    def _control(self, bus_name, body):
        from gi.repository import GLib
        action = body.get("action")
        if action in ACTIONS:
            self._players.call(bus_name, action)
        if isinstance(body.get("setVolume"), (int, float)):
            volume = max(0, min(100, body["setVolume"])) / 100.0
            self._players.set_property(bus_name, "Volume", GLib.Variant("d", volume))
        if isinstance(body.get("Seek"), (int, float)):
            self._players.call(bus_name, "Seek", int(body["Seek"]))
        if isinstance(body.get("SetPosition"), (int, float)):
            metadata = self._players.state(bus_name).get("Metadata") or {}
            track = metadata.get("mpris:trackid")
            if track:
                self._players.call(bus_name, "SetPosition", track, int(body["SetPosition"]) * 1000)
        if isinstance(body.get("setLoopStatus"), str):
            self._players.set_property(bus_name, "LoopStatus", GLib.Variant("s", body["setLoopStatus"]))
        if isinstance(body.get("setShuffle"), bool):
            self._players.set_property(bus_name, "Shuffle", GLib.Variant("b", body["setShuffle"]))

    def _send_player_list(self, daemon, device_id):
        art = daemon.plugins.get_settings(device_id, self.plugin_id).get("album_art", True)
        daemon.send_plugin_packet(self.plugin_id, device_id, "kdeconnect.mpris", {
            "playerList": sorted(self._players.names()),
            "supportAlbumArtPayload": bool(art),
        })

    def _send_art(self, daemon, device_id, display, bus_name, url):
        """The picture the device asked for, if it's still this player's own."""
        if not daemon.plugins.get_settings(device_id, self.plugin_id).get("album_art", True):
            return
        current = (self._players.state(bus_name).get("Metadata") or {}).get("mpris:artUrl")
        path = art_file(url) if url == current else None
        if path is None:
            return
        with open(path, "rb") as stream:
            data = stream.read()
        from satellite.plugins.share import send_bytes
        send_bytes(daemon, device_id, "kdeconnect.mpris",
                   {"player": display, "transferringAlbumArt": True, "albumArtUrl": url}, data)

    def _send_state(self, daemon, device_id, display, bus_name):
        daemon.send_plugin_packet(
            self.plugin_id, device_id, "kdeconnect.mpris",
            now_playing(display, self._players.state(bus_name)))

    @staticmethod
    def send(daemon, device_id, packet_type=None, body=None, **_kwargs):
        if packet_type != "kdeconnect.mpris":
            return False
        return daemon._send_plugin_packet(device_id, packet_type, body or {})

    # -- push changes to connected devices (runs on the GTK main loop) ------

    def _watch(self):
        from gi.repository import Gio
        bus = self._players.bus()
        bus.signal_subscribe(
            None, "org.freedesktop.DBus.Properties", "PropertiesChanged", PATH, PLAYER,
            Gio.DBusSignalFlags.NONE, self._on_properties_changed)
        bus.signal_subscribe(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
            "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE, self._on_name_owner_changed)
        return False

    def _devices(self):
        daemon = self._daemon
        if daemon is None:
            return None, []
        return daemon, [
            d["id"] for d in daemon.devices()
            if d.get("connected") and daemon.plugins.is_enabled_for(d["id"], self.plugin_id)]

    def _on_name_owner_changed(self, _bus, _sender, _path, _iface, _signal, params):
        name = params.unpack()[0]
        if name.startswith(PREFIX) and not name.startswith(OWN_PREFIX):
            daemon, devices = self._devices()
            for device_id in devices:
                self._send_player_list(daemon, device_id)

    def _on_properties_changed(self, bus, sender, _path, _iface, _signal, _params):
        daemon, devices = self._devices()
        if not devices:
            return
        from gi.repository import GLib
        for display, bus_name in list(self._players.names().items()):
            try:
                owner = bus.call_sync(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                    "GetNameOwner", GLib.Variant("(s)", (bus_name,)),
                    GLib.VariantType.new("(s)"), 0, 1000, None).unpack()[0]
            except GLib.Error:
                continue
            if owner == sender:
                for device_id in devices:
                    self._send_state(daemon, device_id, display, bus_name)
                break
