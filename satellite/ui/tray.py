"""System tray icon (XApp.StatusIcon) so Satellite can live in the background."""

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("XApp", "1.0")
from gi.repository import Gtk, XApp


class Tray:
    def __init__(self, on_toggle_window, on_quit):
        self._icon = XApp.StatusIcon()
        self._icon.set_name("satellite")
        theme = Gtk.IconTheme.get_default()
        self._icon.set_icon_name("satellite" if theme.has_icon("satellite") else "network-wireless-symbolic")
        self._icon.set_tooltip_text("Satellite")

        menu = Gtk.Menu()
        show = Gtk.MenuItem(label="Show Satellite")
        show.connect("activate", lambda _i: on_toggle_window(force_show=True))
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _i: on_quit())
        for item in (show, Gtk.SeparatorMenuItem(), quit_item):
            menu.append(item)
        menu.show_all()
        self._icon.set_secondary_menu(menu)
        self._icon.connect("activate", lambda _icon, _button, _time: on_toggle_window())

    def set_visible(self, visible):
        self._icon.set_visible(visible)
