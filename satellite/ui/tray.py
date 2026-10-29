"""System tray icon (XApp.StatusIcon): Satellite's home while the window is closed.

The menu lists every device with the same entries as its ⋮ menu. Entries that
show a dialog open the window first; the others run in the background.
"""

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("XApp", "1.0")
from gi.repository import Gtk, XApp

from satellite.ui.device_menu import device_entries, menu_signature
from satellite.ui.device_page import menu_item
from satellite.ui.widgets import battery_percent
from satellite.i18n import _


def device_label(device):
    if device.get("connected"):
        battery = battery_percent(device)
        return "{} · {}%".format(device["name"], battery) if battery is not None else device["name"]
    if device.get("pair_requested"):
        return _("{} · wants to pair").format(device["name"])
    if device.get("pair_pending"):
        return _("{} · pairing…").format(device["name"])
    if device.get("paired"):
        return _("{} · offline").format(device["name"])
    return _("{} · not paired").format(device["name"])


def device_order(device):
    """Connected first, then pairing requests, offline and new devices."""
    if device.get("connected"):
        rank = 0
    elif device.get("pair_requested"):
        rank = 1
    elif device.get("paired"):
        rank = 2
    else:
        rank = 3
    return (rank, device["name"].lower())


class Tray:
    def __init__(self, window, on_toggle_window, on_quit):
        self._window = window
        self._on_toggle_window = on_toggle_window
        self._on_quit = on_quit
        self._signature = None
        self._pending = None

        self._icon = XApp.StatusIcon()
        self._icon.set_name("satellite")
        theme = Gtk.IconTheme.get_default()
        self._icon.set_icon_name(
            "satellite-symbolic" if theme.has_icon("satellite-symbolic") else "network-wireless-symbolic")
        self._icon.set_tooltip_text(_("Satellite"))
        self._icon.connect("activate", lambda _icon, _button, _time: on_toggle_window())

        self.menu = Gtk.Menu()
        self.menu.connect("hide", self._on_menu_hidden)
        self._icon.set_secondary_menu(self.menu)
        window.device_listeners.append(self.update)
        self.update(window.current_devices())

    def set_visible(self, visible):
        self._icon.set_visible(visible)

    def update(self, devices):
        """Rebuild the menu if what it shows has changed."""
        devices = sorted(devices, key=device_order)
        entries = [(device, device_entries(self._window, device)) for device in devices]
        signature = tuple(
            (device["id"], device_label(device), menu_signature(items)) for device, items in entries)
        if signature == self._signature:
            return
        if self.menu.get_visible():
            self._pending = devices  # rebuilding an open menu would close it
            return
        self._signature = signature
        self._build(entries)

    def _on_menu_hidden(self, _menu):
        if self._pending is not None:
            devices, self._pending = self._pending, None
            self.update(devices)

    def _build(self, entries):
        for child in self.menu.get_children():
            self.menu.remove(child)
        for device, items in entries:
            item = Gtk.MenuItem(label=device_label(device))
            if items:
                submenu = Gtk.Menu()
                for entry in items:
                    submenu.append(menu_item(entry, lambda e, d=device["id"]: self._callback(d, e)))
                item.set_submenu(submenu)
            else:
                item.set_sensitive(False)
            self.menu.append(item)
        if entries:
            self.menu.append(Gtk.SeparatorMenuItem())

        show = Gtk.MenuItem(label=_("Open Satellite"))
        show.connect("activate", lambda _i: self._on_toggle_window(force_show=True))
        self.menu.append(show)
        quit_item = Gtk.MenuItem(label=_("Quit"))
        quit_item.connect("activate", lambda _i: self._on_quit())
        self.menu.append(quit_item)
        self.menu.show_all()

    def _callback(self, device_id, entry):
        if entry.opens_window:
            def run():
                self._window.open_device(device_id)
                entry.callback()
            return run
        if entry.action is not None:
            return lambda: self._window.run_action(device_id, entry.action, background=True)
        return entry.callback
