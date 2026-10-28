"""The device's media players, shown on this desktop through the MPRIS bridge.

Album art comes along like in KDE Connect: when "now playing" names a picture,
Satellite asks for it once, caches it and the bridge offers it to the desktop.
"""

import hashlib
import os
import threading

from satellite.plugins._base import PacketStatePlugin
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True
MAX_ART_SIZE = 5 * 1024 * 1024


def art_dir():
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(cache, "satellite", "albumart")


def art_path(url):
    return os.path.join(art_dir(), hashlib.sha1(url.encode("utf-8")).hexdigest())


class Plugin(PacketStatePlugin):
    plugin_id = "mpris"
    display_name = _("Media player control")
    description = _("Show and control the device's music and videos from this desktop.")
    icon = "audio-x-generic-symbolic"
    incoming_capabilities = ("kdeconnect.mpris",)
    outgoing_capabilities = ("kdeconnect.mpris.request",)
    packet_types = incoming_capabilities
    settings_schema = (
        {"key": "album_art", "label": _("Show album art from the device"), "type": "bool", "default": True},
    )

    def __init__(self, downloader=None):
        self._requested = set()
        self._lock = threading.Lock()
        self._downloader = downloader  # tests replace the payload download

    def handle_packet(self, daemon, link, packet):
        body = packet.get("body", {})
        if packet.get("type") == "kdeconnect.mpris" and body.get("transferringAlbumArt"):
            # Not a state change: the picture asked for below.
            url = body.get("albumArtUrl")
            size = packet.get("payloadSize")
            if isinstance(url, str) and isinstance(size, int) and 0 < size <= MAX_ART_SIZE:
                threading.Thread(target=self._receive_art, args=(daemon, link, packet, url), daemon=True).start()
            return True
        handled = super().handle_packet(daemon, link, packet)
        players = body.get("playerList")
        if handled and isinstance(players, list) and players:
            daemon.send_plugin_packet(
                self.plugin_id,
                link["id"],
                "kdeconnect.mpris.request",
                {"player": players[0], "requestNowPlaying": True, "requestVolume": True},
            )
        url = body.get("albumArtUrl")
        if handled and isinstance(url, str) and url and \
                daemon.plugins.get_settings(link["id"], self.plugin_id).get("album_art", True):
            self._want_art(daemon, link, body.get("player"), url)
        return handled

    def _want_art(self, daemon, link, player, url):
        if os.path.exists(art_path(url)):
            self._set_art(daemon, link["id"], url, art_path(url))
            return
        with self._lock:
            if (link["id"], url) in self._requested:
                return
            self._requested.add((link["id"], url))
        daemon.send_plugin_packet(self.plugin_id, link["id"], "kdeconnect.mpris.request",
                                  {"player": player, "albumArtUrl": url})

    def _receive_art(self, daemon, link, packet, url):
        download = self._downloader
        if download is None:
            from satellite.plugins.share import receive_file

            def download(daemon, link, packet, folder, name):
                return receive_file(daemon, link, packet, folder, quiet=True, name=name)
        os.makedirs(art_dir(), mode=0o700, exist_ok=True)
        path = download(daemon, link, packet, art_dir(), os.path.basename(art_path(url)))
        with self._lock:
            self._requested.discard((link["id"], url))
        if path:
            self._set_art(daemon, link["id"], url, path)

    @staticmethod
    def _set_art(daemon, device_id, url, path):
        with daemon._lock:
            device = daemon._devices.get(device_id)
            if device is None:
                return
            plugin_data = device.setdefault("plugin_data", {})
            if plugin_data.get("mpris_art") == {"url": url, "path": path}:
                return
            plugin_data["mpris_art"] = {"url": url, "path": path}
        daemon._notify_devices_changed()
