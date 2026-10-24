#!/usr/bin/python3
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

import mock_devices
from common import APP_ID, VERSION

ICONS = {
    "phone": "phone-symbolic",
    "tablet": "tablet-symbolic",
    "laptop": "computer-symbolic",
    "desktop": "computer-symbolic",
}


def icon_for(device_type):
    return ICONS.get(device_type, "network-wireless-symbolic")


def section_label(text):
    label = Gtk.Label(xalign=0)
    label.set_margin_top(10)
    label.set_margin_start(10)
    label.set_margin_bottom(4)
    label.set_markup("<b>{}</b>".format(GLib.markup_escape_text(text)))
    label.get_style_context().add_class("dim-label")
    return label


class DeviceRow(Gtk.ListBoxRow):
    def __init__(self, device):
        super().__init__()
        self.device = device

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(10)
        box.set_margin_end(10)

        icon = Gtk.Image.new_from_icon_name(
            icon_for(device["type"]), Gtk.IconSize.LARGE_TOOLBAR
        )
        box.pack_start(icon, False, False, 0)

        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        name_label = Gtk.Label(label=device["name"], xalign=0)
        label_box.pack_start(name_label, False, False, 0)

        if device.get("connected"):
            status = "Connected · {}%".format(device.get("battery", 0))
        else:
            status = "Available"
        status_label = Gtk.Label(label=status, xalign=0)
        status_label.get_style_context().add_class("dim-label")
        label_box.pack_start(status_label, False, False, 0)

        box.pack_start(label_box, True, True, 0)
        self.add(box)


class SatelliteWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Satellite")
        self.set_default_size(760, 480)

        self.current_device = None

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("Satellite")
        self.set_titlebar(header)

        menu_button = Gtk.MenuButton()
        menu_button.set_image(
            Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        )
        menu_button.set_menu_model(self._build_menu())

        self.ping_button = Gtk.Button(label="Send ping")
        self.ping_button.connect("clicked", self.on_ping_clicked)

        self.unpair_button = Gtk.Button(label="Unpair")
        self.unpair_button.connect("clicked", self.on_unpair_clicked)

        header.pack_end(menu_button)
        header.pack_end(self.ping_button)
        header.pack_end(self.unpair_button)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(220)
        paned.pack1(self._build_sidebar(), False, False)

        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        paned.pack2(self.detail, True, False)

        self.add(paned)

        self._show_empty_state()
        self.show_all()
        self.unpair_button.hide()
        self.ping_button.hide()

    def _build_menu(self):
        menu = Gio.Menu()
        menu.append("About", "app.about")
        quit_section = Gio.Menu()
        quit_section.append("Quit", "app.quit")
        menu.append_section(None, quit_section)
        return menu

    def _build_sidebar(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.detected_list = Gtk.ListBox()
        self.detected_list.set_selection_mode(Gtk.SelectionMode.SINGLE)

        self.connected_list = Gtk.ListBox()
        self.connected_list.set_selection_mode(Gtk.SelectionMode.SINGLE)

        self.detected_list.connect("row-selected", self._on_row_selected, self.connected_list)
        self.connected_list.connect("row-selected", self._on_row_selected, self.detected_list)

        inner.pack_start(section_label("DETECTED"), False, False, 0)
        for device in mock_devices.DETECTED:
            self.detected_list.add(DeviceRow(device))
        inner.pack_start(self.detected_list, False, False, 0)

        inner.pack_start(Gtk.Separator(margin_top=8, margin_bottom=4), False, False, 0)

        inner.pack_start(section_label("CONNECTED"), False, False, 0)
        for device in mock_devices.CONNECTED:
            self.connected_list.add(DeviceRow(dict(device, connected=True)))
        inner.pack_start(self.connected_list, False, False, 0)

        scrolled.add(inner)
        return scrolled

    def _on_row_selected(self, listbox, row, other_list):
        if row is not None:
            other_list.unselect_all()
            self._show_device(row.device)
        elif (
            self.detected_list.get_selected_row() is None
            and self.connected_list.get_selected_row() is None
        ):
            self._show_empty_state()

    def _clear_detail(self):
        for child in self.detail.get_children():
            self.detail.remove(child)

    def _show_empty_state(self):
        self._clear_detail()
        self.current_device = None
        self.unpair_button.hide()
        self.ping_button.hide()

        placeholder = Gtk.Label(label="Select a device")
        placeholder.get_style_context().add_class("dim-label")
        placeholder.set_vexpand(True)
        placeholder.set_valign(Gtk.Align.CENTER)
        self.detail.pack_start(placeholder, True, True, 0)
        self.detail.show_all()

    def _show_device(self, device):
        self._clear_detail()
        self.current_device = device
        self.unpair_button.show()
        self.ping_button.show()

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header_box.set_margin_top(24)
        header_box.set_margin_start(24)
        header_box.set_margin_end(24)

        icon = Gtk.Image.new_from_icon_name(icon_for(device["type"]), Gtk.IconSize.DIALOG)
        header_box.pack_start(icon, False, False, 0)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        name_label = Gtk.Label(xalign=0)
        name_label.set_markup(
            "<span size='x-large'>{}</span>".format(GLib.markup_escape_text(device["name"]))
        )
        title_box.pack_start(name_label, False, False, 0)

        if device.get("connected"):
            status_text = "Connected · Battery {}%".format(device.get("battery", 0))
        else:
            status_text = "Detected, not paired"
        status_label = Gtk.Label(label=status_text, xalign=0)
        status_label.get_style_context().add_class("dim-label")
        title_box.pack_start(status_label, False, False, 0)

        header_box.pack_start(title_box, True, True, 0)
        self.detail.pack_start(header_box, False, False, 0)

        if device.get("connected"):
            battery = Gtk.ProgressBar()
            battery.set_fraction(device.get("battery", 0) / 100)
            battery.set_margin_start(24)
            battery.set_margin_end(24)
            battery.set_margin_top(12)
            self.detail.pack_start(battery, False, False, 0)

        self.detail.pack_start(Gtk.Separator(margin_top=18), False, False, 0)
        self.detail.pack_start(section_label("PLUGINS"), False, False, 0)

        plugins_list = Gtk.ListBox()
        plugins_list.set_selection_mode(Gtk.SelectionMode.NONE)
        plugins_list.set_margin_start(24)
        plugins_list.set_margin_end(24)
        for plugin_name in ("Battery", "Clipboard sharing", "Notifications", "Ping"):
            row = Gtk.ListBoxRow()
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            row_box.set_margin_top(6)
            row_box.set_margin_bottom(6)
            row_box.set_margin_start(6)
            row_box.set_margin_end(6)
            row_box.pack_start(Gtk.Label(label=plugin_name, xalign=0), True, True, 0)
            switch = Gtk.Switch(active=bool(device.get("connected")))
            switch.set_valign(Gtk.Align.CENTER)
            row_box.pack_start(switch, False, False, 0)
            row.add(row_box)
            plugins_list.add(row)
        self.detail.pack_start(plugins_list, False, False, 0)

        self.detail.show_all()

    def on_unpair_clicked(self, button):
        if self.current_device:
            print("[stub] unpair requested for {}".format(self.current_device["name"]))

    def on_ping_clicked(self, button):
        if self.current_device:
            print("[stub] ping sent to {}".format(self.current_device["name"]))


class SatelliteApplication(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self.on_about)
        self.add_action(about_action)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_activate(self):
        if self.window is None:
            self.window = SatelliteWindow(self)
        self.window.present()

    def on_about(self, action, param):
        about = Gtk.AboutDialog(transient_for=self.window, modal=True)
        about.set_program_name("Satellite")
        about.set_version(VERSION)
        about.set_comments("KDE Connect protocol client")
        about.set_license_type(Gtk.License.GPL_3_0)
        about.present()


def main():
    app = SatelliteApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
