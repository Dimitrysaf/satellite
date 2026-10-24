"""Expose a connected KDE Connect media player through the desktop MPRIS bus."""

import hashlib

from gi.repository import Gio, GLib


OBJECT_PATH = "/org/mpris/MediaPlayer2"
BUS_NAME = "org.mpris.MediaPlayer2.satellite"
ROOT_INTERFACE = "org.mpris.MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"

ROOT_XML = """
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/><method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="Fullscreen" type="b" access="readwrite"/>
    <property name="CanSetFullscreen" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
</node>
"""

PLAYER_XML = """
<node>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/><method name="Previous"/><method name="Pause"/>
    <method name="PlayPause"/><method name="Stop"/><method name="Play"/>
    <method name="Seek"><arg name="Offset" type="x" direction="in"/></method>
    <method name="SetPosition"><arg name="TrackId" type="o" direction="in"/><arg name="Position" type="x" direction="in"/></method>
    <method name="OpenUri"><arg name="Uri" type="s" direction="in"/></method>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""


class MprisBridge:
    def __init__(self, send_request):
        self.send_request = send_request
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = self.connection.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "RequestName",
            GLib.Variant("(su)", (BUS_NAME, 4)),
            GLib.VariantType.new("(u)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        ).unpack()[0]
        if result not in (1, 4):
            raise RuntimeError("MPRIS bus name is already owned")

        self._registrations = []
        for xml in (ROOT_XML, PLAYER_XML):
            info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
            registration = self.connection.register_object(
                OBJECT_PATH,
                info,
                self._method_call,
                self._get_property,
                self._set_property,
            )
            self._registrations.append(registration)

        self.device_id = None
        self.device_name = "Satellite"
        self.player = ""
        self.media = {}
        self._last_properties = {}

    def update(self, devices):
        selected = None
        for device in devices:
            if not device.get("connected") or not device.get("paired"):
                continue
            plugin = next(
                (
                    item for item in device.get("available_plugins", [])
                    if item.get("id") == "mpris" and item.get("enabled")
                ),
                None,
            )
            if plugin:
                selected = device
                if device.get("plugin_data", {}).get("mpris", {}).get("body", {}).get("isPlaying"):
                    break

        self.device_id = selected["id"] if selected else None
        self.device_name = selected["name"] if selected else "Satellite"
        self.media = (
            selected.get("plugin_data", {}).get("mpris", {}).get("body", {})
            if selected else {}
        )
        players = self.media.get("playerList", [])
        self.player = self.media.get("player") or (players[0] if players else "")

        changed = {}
        for interface, names in (
            (ROOT_INTERFACE, ("Identity",)),
            (PLAYER_INTERFACE, (
                "PlaybackStatus", "Metadata", "Position", "Volume", "Shuffle",
                "LoopStatus", "CanGoNext", "CanGoPrevious", "CanPlay", "CanPause",
                "CanSeek", "CanControl",
            )),
        ):
            values = {name: self._property_value(interface, name) for name in names}
            for name, value in values.items():
                signature = (interface, name)
                if signature not in self._last_properties or self._last_properties[signature] != value:
                    changed[name] = value
                    self._last_properties[signature] = value
            if changed:
                self.connection.emit_signal(
                    None,
                    OBJECT_PATH,
                    "org.freedesktop.DBus.Properties",
                    "PropertiesChanged",
                    GLib.Variant("(sa{sv}as)", (interface, changed, [])),
                )
                changed = {}

    def close(self):
        for registration in self._registrations:
            self.connection.unregister_object(registration)
        self.connection.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ReleaseName",
            GLib.Variant("(s)", (BUS_NAME,)),
            GLib.VariantType.new("(u)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )

    def _method_call(self, _connection, _sender, _path, interface, method, params, invocation):
        args = params.unpack()
        if interface == ROOT_INTERFACE:
            invocation.return_value(GLib.Variant("()", ()))
            return
        if interface != PLAYER_INTERFACE:
            invocation.return_dbus_error("org.mpris.MediaPlayer2.Error", "Unknown interface")
            return

        action = {
            "Next": "Next", "Previous": "Previous", "Pause": "Pause",
            "PlayPause": "PlayPause", "Stop": "Stop", "Play": "Play",
        }.get(method)
        body = {"player": self.player, "action": action} if action else {"player": self.player}
        if method == "Seek":
            body["Seek"] = int(args[0])
        elif method == "SetPosition":
            body["SetPosition"] = int(args[1]) // 1000
        elif method == "OpenUri":
            body = {"url": args[0]}
            self.send_request("share", self.device_id, "kdeconnect.share.request", body)
            invocation.return_value(GLib.Variant("()", ()))
            return
        else:
            body["player"] = self.player
        self.send_request("mpris", self.device_id, "kdeconnect.mpris.request", body)
        invocation.return_value(GLib.Variant("()", ()))

    def _set_property(self, _connection, _sender, _path, interface, name, value):
        if interface != PLAYER_INTERFACE or not self.device_id:
            return False
        body = {"player": self.player}
        unpacked = value.unpack()
        if name == "Volume":
            body["setVolume"] = round(max(0.0, unpacked) * 100)
        elif name == "Shuffle":
            body["setShuffle"] = bool(unpacked)
        elif name == "LoopStatus":
            body["setLoopStatus"] = str(unpacked)
        elif name == "Rate":
            return True
        else:
            return False
        self.send_request("mpris", self.device_id, "kdeconnect.mpris.request", body)
        return True

    def _get_property(self, _connection, _sender, _path, interface, name):
        return self._property_value(interface, name)

    def _property_value(self, interface, name):
        media = self.media
        playing = bool(media.get("isPlaying"))
        if interface == ROOT_INTERFACE:
            root_values = {
                "CanQuit": GLib.Variant("b", False),
                "Fullscreen": GLib.Variant("b", False),
                "CanSetFullscreen": GLib.Variant("b", False),
                "CanRaise": GLib.Variant("b", False),
                "HasTrackList": GLib.Variant("b", False),
                "Identity": GLib.Variant("s", "KDE Connect · {}".format(self.device_name)),
                "DesktopEntry": GLib.Variant("s", "satellite"),
                "SupportedUriSchemes": GLib.Variant("as", []),
                "SupportedMimeTypes": GLib.Variant("as", []),
            }
            return root_values.get(name)

        track_hash = hashlib.sha1(
            "{}:{}:{}:{}".format(
                self.device_id or "none", self.player,
                media.get("artist", ""), media.get("title", ""),
            ).encode("utf-8")
        ).hexdigest()
        metadata = {
            "mpris:trackid": GLib.Variant("o", "/org/mpris/MediaPlayer2/track/" + track_hash),
            "xesam:title": GLib.Variant("s", str(media.get("title") or media.get("nowPlaying") or "")),
            "xesam:artist": GLib.Variant("as", [str(media["artist"])] if media.get("artist") else []),
            "xesam:album": GLib.Variant("s", str(media.get("album", ""))),
        }
        if isinstance(media.get("length"), (int, float)):
            metadata["mpris:length"] = GLib.Variant("x", int(media["length"] * 1000))
        player_values = {
            "PlaybackStatus": GLib.Variant("s", "Playing" if playing else ("Paused" if media else "Stopped")),
            "LoopStatus": GLib.Variant("s", str(media.get("loopStatus", "None"))),
            "Rate": GLib.Variant("d", 1.0),
            "Shuffle": GLib.Variant("b", bool(media.get("shuffle", False))),
            "Metadata": GLib.Variant("a{sv}", metadata),
            "Volume": GLib.Variant("d", float(media.get("volume", 100)) / 100.0),
            "Position": GLib.Variant("x", int(media.get("pos", 0) * 1000)),
            "MinimumRate": GLib.Variant("d", 1.0),
            "MaximumRate": GLib.Variant("d", 1.0),
            "CanGoNext": GLib.Variant("b", bool(media.get("canGoNext", False))),
            "CanGoPrevious": GLib.Variant("b", bool(media.get("canGoPrevious", False))),
            "CanPlay": GLib.Variant("b", bool(media.get("canPlay", False))),
            "CanPause": GLib.Variant("b", bool(media.get("canPause", False))),
            "CanSeek": GLib.Variant("b", bool(media.get("canSeek", False))),
            "CanControl": GLib.Variant("b", bool(self.device_id and self.player)),
        }
        return player_values.get(name)
