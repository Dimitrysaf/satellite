"""Add devices by IP: addresses Satellite contacts directly, worded like KDE Connect for Android."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from satellite.i18n import _


class CustomDevicesDialog(Gtk.Dialog):
    def __init__(self, parent, daemon):
        super().__init__(title=_("Add devices by IP"), transient_for=parent, modal=True)
        self.set_default_size(420, -1)
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        self._daemon = daemon

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, border_width=16)
        self.get_content_area().add(box)
        help_text = Gtk.Label(
            label=_("If your device is not automatically detected, you can add its IP address or hostname."),
            xalign=0, wrap=True, max_width_chars=48)
        help_text.get_style_context().add_class("dim-label")
        box.pack_start(help_text, False, False, 0)

        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.get_style_context().add_class("frame")
        box.pack_start(self._list, False, False, 0)

        self._entry = Gtk.Entry(placeholder_text=_("Hostname or IP address"), hexpand=True)
        self._entry.connect("activate", self._add)
        add = Gtk.Button(label=_("Add device"))
        add.connect("clicked", self._add)
        row = Gtk.Box(spacing=8)
        row.pack_start(self._entry, True, True, 0)
        row.pack_start(add, False, False, 0)
        box.pack_start(row, False, False, 0)

        self.show_all()
        self._refresh()

    def _addresses(self):
        return self._daemon.discovery.get("custom_devices")

    def _save(self, addresses):
        self._daemon.discovery.set("custom_devices", addresses)
        self._daemon.apply_discovery_settings()  # contacts the new list right away
        self._refresh()

    def _add(self, *_args):
        text = self._entry.get_text().strip()
        if text:
            self._save(self._addresses() + [text])
            self._entry.set_text("")

    def _refresh(self):
        for child in self._list.get_children():
            self._list.remove(child)
        for address in self._addresses():
            row = Gtk.Box(spacing=8, margin=6)
            row.pack_start(Gtk.Label(label=address, xalign=0), True, True, 6)
            remove = Gtk.Button.new_from_icon_name("list-remove-symbolic", Gtk.IconSize.BUTTON)
            remove.set_tooltip_text(_("Remove"))
            remove.connect("clicked", lambda _b, a=address: self._save([x for x in self._addresses() if x != a]))
            row.pack_start(remove, False, False, 0)
            self._list.add(row)
        if self._addresses():
            self._list.show_all()
        else:
            self._list.hide()
