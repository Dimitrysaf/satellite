"""The right-hand page for one device: header, status and plugins."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from satellite.ui.device_menu import device_entries
from satellite.ui.pairing_prompt import PairingPrompt
from satellite.ui.plugin_list import PluginList
from satellite.ui.widgets import battery_percent, icon_for, long_status, section_label
from satellite.i18n import _


class DevicePage(Gtk.Box):
    """Rebuilds itself from a device dict. ``controller`` is the main window.

    The controller provides: ``daemon``, ``pair``, ``accept_pair``,
    ``deny_pair``, ``unpair``, ``rename``, ``show_encryption``,
    ``toggle_plugin``, ``configure_plugin`` and ``run_action`` (all
    taking the device id first where relevant).
    """

    def __init__(self, controller):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._controller = controller
        self.menu_button = None

    def show_device(self, device):
        for child in self.get_children():
            self.remove(child)
        controller = self._controller
        device_id = device["id"]
        connected = bool(device.get("connected"))

        self.pack_start(self._header(device), False, True, 0)

        if not connected:
            self.pack_start(PairingPrompt(
                device,
                on_pair=lambda: controller.pair(device_id),
                on_accept=lambda: controller.accept_pair(device_id),
                on_deny=lambda: controller.deny_pair(device_id),
            ), True, True, 0)
            self.show_all()
            return

        self._add_status(device)

        plugins = device.get("available_plugins", [])
        if plugins:
            self.pack_start(Gtk.Separator(margin_top=18), False, False, 0)
            self.pack_start(section_label(_("PLUGINS")), False, False, 0)
            self.pack_start(PluginList(
                plugins,
                lambda plugin_id, state: controller.toggle_plugin(device_id, plugin_id, state),
                lambda plugin_id: controller.configure_plugin(device_id, plugin_id),
            ), False, True, 0)
        self.show_all()

    def _header(self, device):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.pack_start(Gtk.Image.new_from_icon_name(icon_for(device["type"]), Gtk.IconSize.DIALOG), False, False, 0)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        name = Gtk.Label(xalign=0)
        name.set_markup("<span size='x-large'>{}</span>".format(GLib.markup_escape_text(device["name"])))
        name.set_selectable(True)
        titles.pack_start(name, False, False, 0)
        status = Gtk.Label(label=long_status(device), xalign=0)
        status.get_style_context().add_class("dim-label")
        titles.pack_start(status, False, False, 0)
        box.pack_start(titles, True, True, 0)

        if device.get("secure") or device.get("paired"):
            box.pack_end(self._menu_button(device), False, False, 0)
        return box

    def _menu_button(self, device):
        menu = Gtk.Menu()
        for entry in device_entries(self._controller, device, include_pairing=False):
            menu.append(menu_item(entry))
        menu.show_all()

        button = Gtk.MenuButton()
        button.set_direction(Gtk.ArrowType.NONE)
        button.set_image(Gtk.Image.new_from_icon_name("view-more-symbolic", Gtk.IconSize.BUTTON))
        button.set_tooltip_text(_("Device actions"))
        button.set_valign(Gtk.Align.CENTER)
        button.set_popup(menu)
        self.menu_button = button
        return button

    def _add_status(self, device):
        """Battery with its bar, then plugin statuses as one row of icons."""
        battery = battery_percent(device)
        if battery is not None:
            charging = bool(device.get("isCharging"))
            icon = "battery-good-charging-symbolic" if charging else "battery-good-symbolic"
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row.pack_start(status_item(
                icon, (_("Battery · {}% · Charging") if charging else _("Battery · {}%")).format(battery)
            ), False, False, 0)
            bar = Gtk.ProgressBar()
            bar.set_fraction(min(100, battery) / 100.0)
            row.pack_start(bar, False, False, 0)
            self._pack_status(row)

        statuses = self._controller.daemon.plugins.statuses_for(device)
        if statuses:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
            for _plugin_id, icon, text in statuses:
                line.pack_start(status_item(icon, text), False, False, 0)
            self._pack_status(line)

    def _pack_status(self, widget):
        widget.set_margin_start(24)
        widget.set_margin_end(24)
        widget.set_margin_top(14)
        self.pack_start(widget, False, False, 0)


def menu_item(entry, wrap=None):
    """A Gtk.MenuItem for a MenuEntry (or a separator for None).

    ``wrap(entry)`` may return a different callback, as the tray does.
    """
    if entry is None:
        return Gtk.SeparatorMenuItem()
    item = Gtk.MenuItem(label=entry.label)
    item.set_sensitive(entry.sensitive)
    if entry.callback is not None:
        callback = wrap(entry) if wrap else entry.callback
        item.connect("activate", lambda _i: callback())
    return item


def status_item(icon, text):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU), False, False, 0)
    box.pack_start(Gtk.Label(label=text, xalign=0), False, False, 0)
    return box
