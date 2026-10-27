"""The right-hand page for one device: header, buttons, status and plugins."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from satellite.ui.action_bar import ActionBar
from satellite.ui.media_controls import MediaControls
from satellite.ui.pairing_prompt import PairingPrompt
from satellite.ui.plugin_list import PluginList
from satellite.ui.widgets import battery_percent, icon_for, long_status, section_label


class DevicePage(Gtk.Box):
    """Rebuilds itself from a device dict. ``controller`` is the main window.

    The controller provides: ``daemon``, ``pair``, ``accept_pair``,
    ``deny_pair``, ``unpair``, ``rename``, ``show_encryption``,
    ``toggle_plugin``, ``configure_plugin``, ``media_action`` and
    ``run_action`` (all taking the device id first where relevant).
    """

    def __init__(self, controller):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._controller = controller

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

        actions = controller.daemon.plugins.actions_for(device)
        if actions:
            self.pack_start(ActionBar(
                actions, lambda action: controller.run_action(device_id, action)
            ), False, True, 0)

        self._add_status_rows(device)

        media = device.get("plugin_data", {}).get("mpris", {}).get("body", {})
        if media:
            self.pack_start(MediaControls(
                media, lambda player, act: controller.media_action(device_id, player, act)
            ), False, False, 0)

        plugins = device.get("available_plugins", [])
        if plugins:
            self.pack_start(Gtk.Separator(margin_top=18), False, False, 0)
            self.pack_start(section_label("PLUGINS"), False, False, 0)
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
        controller, device_id = self._controller, device["id"]
        menu = Gtk.Menu()

        def add(label, callback):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _i: callback())
            menu.append(item)

        add("Rename…", lambda: controller.rename(device_id))
        if device.get("secure"):
            add("Encryption info", lambda: controller.show_encryption(device_id))
        if device.get("paired"):
            menu.append(Gtk.SeparatorMenuItem())
            add("Unpair", lambda: controller.unpair(device_id))
        menu.show_all()

        button = Gtk.MenuButton()
        button.set_direction(Gtk.ArrowType.NONE)
        button.set_image(Gtk.Image.new_from_icon_name("view-more-symbolic", Gtk.IconSize.BUTTON))
        button.set_tooltip_text("Device actions")
        button.set_valign(Gtk.Align.CENTER)
        button.set_popup(menu)
        return button

    def _status_row(self, icon, text, fraction=None):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU), False, False, 0)
        line.pack_start(Gtk.Label(label=text, xalign=0), True, True, 0)
        row.pack_start(line, False, False, 0)
        if fraction is not None:
            bar = Gtk.ProgressBar()
            bar.set_fraction(fraction)
            row.pack_start(bar, False, False, 0)
        row.set_margin_start(24)
        row.set_margin_end(24)
        row.set_margin_top(12)
        self.pack_start(row, False, False, 0)

    def _add_status_rows(self, device):
        battery = battery_percent(device)
        if battery is not None:
            charging = bool(device.get("isCharging"))
            icon = "battery-good-charging-symbolic" if charging else "battery-good-symbolic"
            self._status_row(
                icon, "Battery · {}%{}".format(battery, " · Charging" if charging else ""),
                min(100, battery) / 100.0,
            )
        for _plugin_id, icon, text in self._controller.daemon.plugins.statuses_for(device):
            self._status_row(icon, text)
        if device.get("last_ping"):
            self._status_row("mail-send-symbolic", "Last ping: {}".format(device["last_ping"]))
