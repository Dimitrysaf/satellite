"""Now-playing card with previous / play-pause / next buttons."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class MediaControls(Gtk.Frame):
    def __init__(self, media, on_action):
        super().__init__(label="Media")
        self.set_margin_start(24)
        self.set_margin_end(24)
        self.set_margin_top(12)

        player = media.get("player")
        if not player:
            players = media.get("playerList", [])
            player = players[0] if players else None
        title = media.get("title") or media.get("nowPlaying") or "No track information"
        artist = media.get("artist") or media.get("album")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_border_width(10)
        content.pack_start(Gtk.Label(label=title, xalign=0), False, False, 0)
        if artist:
            artist_label = Gtk.Label(label=artist, xalign=0)
            artist_label.get_style_context().add_class("dim-label")
            content.pack_start(artist_label, False, False, 0)

        if player:
            controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            controls.set_halign(Gtk.Align.START)
            playing = bool(media.get("isPlaying"))
            for icon, action, tooltip in (
                ("media-skip-backward-symbolic", "Previous", "Previous track"),
                ("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic",
                 "PlayPause", "Pause" if playing else "Play"),
                ("media-skip-forward-symbolic", "Next", "Next track"),
            ):
                button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
                button.set_tooltip_text(tooltip)
                button.connect("clicked", lambda _b, a=action: on_action(player, a))
                controls.pack_start(button, False, False, 0)
            content.pack_start(controls, False, False, 0)
        self.add(content)
