#!/usr/bin/python3
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("XApp", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, XApp

from common import APP_ID, VERSION
from daemon import SatelliteDaemon
from mpris_bridge import MprisBridge

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
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(10)
        box.set_margin_end(10)

        self.icon = Gtk.Image.new_from_icon_name(
            icon_for(device["type"]), Gtk.IconSize.LARGE_TOOLBAR
        )
        box.pack_start(self.icon, False, False, 0)

        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        self.name_label = Gtk.Label(xalign=0)
        label_box.pack_start(self.name_label, False, False, 0)

        self.status_label = Gtk.Label(xalign=0)
        self.status_label.get_style_context().add_class("dim-label")
        label_box.pack_start(self.status_label, False, False, 0)

        box.pack_start(label_box, True, True, 0)
        self.add(box)
        self.update(device)

    @staticmethod
    def _status(device):
        battery = device.get("battery")
        battery_text = (
            "{}%".format(battery)
            if isinstance(battery, (int, float)) and battery >= 0
            else None
        )
        if device.get("connected"):
            if battery_text:
                return "Connected · {}".format(battery_text)
            return "Connected"
        if device.get("pair_requested"):
            return "Pairing request"
        if device.get("pair_pending"):
            return "Pair request sent"
        if device.get("paired") and not device.get("secure"):
            if battery_text:
                return "Offline · {}".format(battery_text)
            return "Offline"
        if device.get("secure"):
            return "Secure link established"
        return "Available"

    def update(self, device):
        self.device = device
        self.icon.set_from_icon_name(icon_for(device["type"]), Gtk.IconSize.LARGE_TOOLBAR)
        self.name_label.set_text(device["name"])
        self.status_label.set_text(self._status(device))


class SatelliteWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Satellite")
        self.set_default_size(760, 480)

        self.current_device = None
        self._narrow_layout = None
        self._mobile_showing_detail = False
        self._clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self._last_clipboard_text = None
        self._clipboard_sent = {}
        self._clipboard_received = {}
        self._transfer_notification_ids = {}
        self._transfer_popups = {}
        self._active_transfers = {}
        self.discovery = SatelliteDaemon(
            self._on_devices_changed, self._on_transfer_progress
        )
        try:
            self._notifications_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self._notifications_bus = None
        try:
            self.media_bridge = MprisBridge(self._send_mpris_request)
        except (GLib.Error, RuntimeError) as error:
            self.media_bridge = None
            print("Could not register Satellite's MPRIS player: {}".format(error), flush=True)

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("Satellite")
        self.set_titlebar(header)

        menu_button = Gtk.MenuButton()
        menu_button.set_direction(Gtk.ArrowType.NONE)
        menu_button.set_use_popover(False)
        menu_icon = Gtk.DrawingArea()
        menu_icon.set_size_request(18, 18)
        menu_icon.connect("draw", self._draw_menu_icon)
        menu_button.set_image(menu_icon)
        menu_button.set_menu_model(self._build_menu())

        self.refresh_button = Gtk.Button()
        self.refresh_button.set_image(
            Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        )
        self.refresh_button.set_tooltip_text("Refresh devices")
        self.refresh_button.connect("clicked", self.on_refresh_clicked)
        header.pack_start(self.refresh_button)

        self.back_button = Gtk.Button()
        self.back_button.set_image(
            Gtk.Image.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        )
        self.back_button.set_tooltip_text("Back to devices")
        self.back_button.connect("clicked", self.on_mobile_back_clicked)
        header.pack_start(self.back_button)

        header.pack_end(menu_button)

        self.layout = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.sidebar = self._build_sidebar()
        self.sidebar.set_size_request(220, -1)
        self.layout.pack_start(self.sidebar, False, True, 0)
        self.sidebar_divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.layout.pack_start(self.sidebar_divider, False, False, 0)

        self.detail_scroller = Gtk.ScrolledWindow()
        self.detail_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.detail_scroller.set_shadow_type(Gtk.ShadowType.NONE)
        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.detail_scroller.add(self.detail)
        self.layout.pack_start(self.detail_scroller, True, True, 0)

        self.device_actions_button = Gtk.MenuButton()
        self.device_actions_button.set_direction(Gtk.ArrowType.NONE)
        self.device_actions_button.set_image(
            Gtk.Image.new_from_icon_name("view-more-symbolic", Gtk.IconSize.BUTTON)
        )
        self.device_actions_button.set_tooltip_text("Device actions")

        self.add(self.layout)
        self.connect("size-allocate", self._on_size_allocate)

        self._show_empty_state()
        self.show_all()
        self.device_actions_button.hide()
        self.back_button.hide()
        self._refresh_devices(self.discovery.devices())
        self.discovery.start()
        GLib.timeout_add(1000, self._sync_clipboard)
        self.connect("destroy", self._on_destroy)

    @staticmethod
    def _draw_menu_icon(widget, context):
        allocation = widget.get_allocation()
        color = widget.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        context.set_line_width(1.8)
        context.set_line_cap(1)  # Cairo LINE_CAP_ROUND
        context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        for y in (4.5, 9, 13.5):
            context.move_to(3, y)
            context.line_to(allocation.width - 3, y)
            context.stroke()
        return False

    def _build_menu(self):
        menu = Gio.Menu()
        menu.append("About", "app.about")
        quit_section = Gio.Menu()
        quit_section.append("Quit", "app.quit")
        menu.append_section(None, quit_section)
        return menu

    def on_refresh_clicked(self, _button):
        self.refresh_button.set_sensitive(False)
        self.discovery.refresh()
        GLib.timeout_add(2000, self._finish_refresh)

    def _finish_refresh(self):
        self.refresh_button.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _on_size_allocate(self, _widget, allocation):
        narrow = allocation.width < 640
        if narrow != self._narrow_layout:
            self._narrow_layout = narrow
            if narrow:
                self._mobile_showing_detail = bool(self.current_device)
            self._update_mobile_layout()

    def _update_mobile_layout(self):
        if not self._narrow_layout:
            self.layout.set_orientation(Gtk.Orientation.HORIZONTAL)
            self.layout.set_child_packing(
                self.sidebar, False, True, 0, Gtk.PackType.START
            )
            self.sidebar.set_size_request(220, -1)
            self.sidebar.show()
            self.sidebar_divider.show()
            self.detail_scroller.show()
            self.back_button.hide()
            self.refresh_button.show()
            return

        self.layout.set_orientation(Gtk.Orientation.VERTICAL)
        self.layout.set_child_packing(
            self.sidebar, True, True, 0, Gtk.PackType.START
        )
        self.sidebar.set_size_request(-1, -1)
        if self._mobile_showing_detail:
            self.sidebar.hide()
            self.sidebar_divider.hide()
            self.detail_scroller.show()
            self.back_button.show()
            self.refresh_button.hide()
        else:
            self.sidebar.show()
            self.sidebar_divider.hide()
            self.detail_scroller.hide()
            self.back_button.hide()
            self.refresh_button.show()

    def on_mobile_back_clicked(self, _button):
        self._mobile_showing_detail = False
        self._update_mobile_layout()

    def _build_sidebar(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.detected_list = Gtk.ListBox()
        self.detected_list.set_selection_mode(Gtk.SelectionMode.SINGLE)

        self.connected_list = Gtk.ListBox()
        self.connected_list.set_selection_mode(Gtk.SelectionMode.SINGLE)

        self.offline_list = Gtk.ListBox()
        self.offline_list.set_selection_mode(Gtk.SelectionMode.SINGLE)

        for listbox in (self.connected_list, self.offline_list, self.detected_list):
            listbox.connect("row-selected", self._on_row_selected)

        self.connected_heading = section_label("CONNECTED")
        inner.pack_start(self.connected_heading, False, False, 0)
        inner.pack_start(self.connected_list, False, False, 0)

        self.connected_separator = Gtk.Separator(margin_top=8, margin_bottom=4)
        inner.pack_start(self.connected_separator, False, False, 0)

        self.offline_heading = section_label("OFFLINE")
        inner.pack_start(self.offline_heading, False, False, 0)
        inner.pack_start(self.offline_list, False, False, 0)

        self.offline_separator = Gtk.Separator(margin_top=8, margin_bottom=4)
        inner.pack_start(self.offline_separator, False, False, 0)

        self.detected_heading = section_label("DETECTED")
        inner.pack_start(self.detected_heading, False, False, 0)
        inner.pack_start(self.detected_list, False, False, 0)

        self.no_devices_label = Gtk.Label(label="No devices detected")
        self.no_devices_label.get_style_context().add_class("dim-label")
        self.no_devices_label.set_margin_top(24)
        self.no_devices_label.set_margin_bottom(24)
        inner.pack_start(self.no_devices_label, False, False, 0)

        scrolled.add(inner)
        return scrolled

    def _on_devices_changed(self, devices):
        GLib.idle_add(self._refresh_devices, devices)

    def _refresh_devices(self, devices):
        if self.media_bridge:
            self.media_bridge.update(devices)

        for device in devices:
            clipboard_data = device.get("plugin_data", {}).get("clipboard", {})
            content = clipboard_data.get("body", {}).get("content")
            received_signature = (clipboard_data.get("id"), content)
            if (
                isinstance(content, str)
                and received_signature != self._clipboard_received.get(device["id"])
            ):
                self._clipboard_received[device["id"]] = received_signature
                self._last_clipboard_text = content
                self._clipboard.set_text(content, -1)
                self._clipboard_sent[device["id"]] = content

        selected_id = self.current_device.get("id") if self.current_device else None
        previous_detail = self.current_device
        previous_signature = self._device_signature(previous_detail)
        old_rows = {}
        for listbox in (self.detected_list, self.offline_list, self.connected_list):
            for row in listbox.get_children():
                old_rows[row.device["id"]] = row

        present_ids = set()
        for device in devices:
            device_id = device["id"]
            present_ids.add(device_id)
            if device.get("connected"):
                target_list = self.connected_list
            elif device.get("paired"):
                target_list = self.offline_list
            else:
                target_list = self.detected_list
            row = old_rows.pop(device_id, None)
            if row is None:
                row = DeviceRow(device)
                target_list.add(row)
            else:
                row.update(device)
                if row.get_parent() is not target_list:
                    row.get_parent().remove(row)
                    target_list.add(row)
            row.show_all()
            if device_id == selected_id:
                self.current_device = device
                if row.get_parent().get_selected_row() is not row:
                    row.get_parent().select_row(row)

        for row in old_rows.values():
            row.get_parent().remove(row)

        self.detected_list.show_all()
        self.offline_list.show_all()
        self.connected_list.show_all()
        has_detected = any(
            not device.get("connected") and not device.get("paired")
            for device in devices
        )
        has_offline = any(
            device.get("paired") and not device.get("connected")
            for device in devices
        )
        has_connected = any(device.get("connected") for device in devices)
        self.detected_heading.set_visible(has_detected)
        self.detected_list.set_visible(has_detected)
        self.offline_heading.set_visible(has_offline)
        self.offline_list.set_visible(has_offline)
        self.connected_heading.set_visible(has_connected)
        self.connected_list.set_visible(has_connected)
        self.connected_separator.set_visible(has_connected and (has_offline or has_detected))
        self.offline_separator.set_visible(has_offline and has_detected)
        self.no_devices_label.set_visible(not has_detected and not has_offline and not has_connected)
        if selected_id and selected_id in present_ids:
            selected = next(device for device in devices if device["id"] == selected_id)
            if self._device_signature(selected) != previous_signature:
                self._show_device(selected, preserve_scroll=True)
            else:
                self.current_device = selected
        self._update_mobile_layout()
        return GLib.SOURCE_REMOVE

    def _sync_clipboard(self):
        content = self._clipboard.wait_for_text()
        if content is None:
            return GLib.SOURCE_CONTINUE
        if content != self._last_clipboard_text:
            self._last_clipboard_text = content

        for device in self.discovery.devices():
            device_id = device["id"]
            clipboard_plugin = next(
                (
                    plugin for plugin in device.get("available_plugins", [])
                    if plugin.get("id") == "clipboard"
                ),
                None,
            )
            if (
                not device.get("connected")
                or not clipboard_plugin
                or not clipboard_plugin.get("enabled")
                or self._clipboard_sent.get(device_id) == content
            ):
                continue
            if self.discovery.send_plugin_packet(
                "clipboard", device_id, "kdeconnect.clipboard", {"content": content}
            ):
                self._clipboard_sent[device_id] = content
        return GLib.SOURCE_CONTINUE

    def _send_mpris_request(self, plugin_id, device_id, packet_type, body):
        if not device_id:
            return False
        return self.discovery.send_plugin_packet(plugin_id, device_id, packet_type, body)

    def _on_destroy(self, *_args):
        self.discovery.stop()
        XApp.set_window_progress(self, 0)
        if self.media_bridge:
            self.media_bridge.close()

    @staticmethod
    def _format_bytes(amount):
        value = float(amount)
        for unit in ("B", "KiB", "MiB", "GiB"):
            if value < 1024 or unit == "GiB":
                return "{} {}".format(
                    int(value) if unit == "B" else "{:.1f}".format(value), unit
                )
            value /= 1024

    def _on_transfer_progress(self, progress):
        GLib.idle_add(self._show_transfer_progress, progress)

    def _show_transfer_progress(self, progress):
        self._update_transfer_popup(progress)
        transfer_id = progress["id"]
        if progress.get("finished"):
            self._active_transfers.pop(transfer_id, None)
        else:
            self._active_transfers[transfer_id] = progress.get("percent", 0)
        XApp.set_window_progress(self, max(self._active_transfers.values(), default=0))

        if self._notifications_bus is None:
            return GLib.SOURCE_REMOVE

        notification_id = self._transfer_notification_ids.get(transfer_id, 0)
        filename = progress["filename"]
        received = self._format_bytes(progress["received"])
        total = self._format_bytes(progress["total"])
        percent = max(0, progress.get("percent", 0))
        if progress.get("error"):
            summary = "File receive failed"
            body = "{} · {}".format(filename, progress["error"])
            timeout = 8000
        elif progress.get("path"):
            summary = "File received"
            body = "{} · {} / {} · 100% · saved to {}".format(
                filename, received, total, os.path.dirname(progress["path"])
            )
            timeout = 6000
        else:
            summary = "Receiving file from {}".format(progress["device_name"])
            body = "{} · {} / {} · {}%".format(filename, received, total, percent)
            timeout = 0

        hints = {
            "value": GLib.Variant("i", percent),
        }
        try:
            reply = self._notifications_bus.call_sync(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                GLib.Variant(
                    "(susssasa{sv}i)",
                    (
                        "Satellite",
                        notification_id,
                        "folder-download-symbolic",
                        summary,
                        body,
                        [],
                        hints,
                        timeout,
                    ),
                ),
                GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            self._transfer_notification_ids[transfer_id] = reply.unpack()[0]
        except GLib.Error as error:
            print("Could not update the file transfer notification: {}".format(error), flush=True)

        if progress.get("finished"):
            self._transfer_notification_ids.pop(transfer_id, None)
        return GLib.SOURCE_REMOVE

    def _update_transfer_popup(self, progress):
        transfer_id = progress["id"]
        popup_data = self._transfer_popups.get(transfer_id)
        if popup_data is None:
            popup = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            popup.set_title("Incoming file")
            popup.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
            popup.set_decorated(False)
            popup.set_resizable(False)
            popup.set_skip_taskbar_hint(True)
            popup.set_skip_pager_hint(True)
            popup.set_keep_above(True)
            popup.set_accept_focus(False)
            popup.set_focus_on_map(False)

            frame = Gtk.Frame()
            frame.set_margin_start(12)
            frame.set_margin_end(12)
            frame.set_margin_top(12)
            frame.set_margin_bottom(12)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            content.set_margin_start(14)
            content.set_margin_end(14)
            content.set_margin_top(12)
            content.set_margin_bottom(12)
            heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            title = Gtk.Label(xalign=0)
            title.set_use_markup(True)
            close_button = Gtk.Button.new_from_icon_name(
                "window-close-symbolic", Gtk.IconSize.BUTTON
            )
            close_button.set_relief(Gtk.ReliefStyle.NONE)
            close_button.set_tooltip_text("Hide transfer progress")
            close_button.connect("clicked", lambda *_: popup.hide())
            heading.pack_start(title, True, True, 0)
            heading.pack_end(close_button, False, False, 0)
            details = Gtk.Label(xalign=0)
            details.get_style_context().add_class("dim-label")
            progress_bar = Gtk.ProgressBar()
            progress_bar.set_show_text(True)
            content.pack_start(heading, False, False, 0)
            content.pack_start(details, False, False, 0)
            content.pack_start(progress_bar, False, False, 0)
            frame.add(content)
            popup.add(frame)
            popup_data = {
                "window": popup,
                "title": title,
                "details": details,
                "progress": progress_bar,
            }
            self._transfer_popups[transfer_id] = popup_data

        percent = max(0, min(100, progress.get("percent", 0)))
        popup_data["title"].set_markup(
            "<b>{}</b>".format(GLib.markup_escape_text(progress["filename"]))
        )
        details = "{} · {} of {} · {}%".format(
            progress["device_name"],
            self._format_bytes(progress["received"]),
            self._format_bytes(progress["total"]),
            percent,
        )
        if progress.get("path"):
            details = "Saved to {}".format(os.path.dirname(progress["path"]))
        elif progress.get("error"):
            details = "Transfer failed · {}".format(progress["error"])
        popup_data["details"].set_text(details)
        popup_data["progress"].set_fraction(percent / 100.0)
        popup_data["progress"].set_text("{}%".format(percent))
        was_visible = popup_data["window"].get_visible()
        popup_data["window"].show_all()
        if not was_visible:
            popup_data["window"].present()

        if progress.get("finished"):
            GLib.timeout_add_seconds(6, self._close_transfer_popup, transfer_id)

    def _close_transfer_popup(self, transfer_id):
        popup_data = self._transfer_popups.pop(transfer_id, None)
        if popup_data:
            popup_data["window"].destroy()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _device_signature(device):
        if not device:
            return None
        return tuple(
            (key, tuple(device.get(key, [])) if key == "plugins" else device.get(key))
            for key in (
                "id", "name", "type", "connected", "secure", "paired",
                "pair_requested", "pair_pending", "verification_code",
                "battery", "isCharging", "last_ping", "plugins",
                "available_plugins",
                "incomingCapabilities", "plugin_data",
            )
        )

    def _on_row_selected(self, listbox, row):
        if row is not None:
            for other_list in (self.connected_list, self.offline_list, self.detected_list):
                if other_list is not listbox:
                    other_list.unselect_all()
            if self._narrow_layout:
                self._mobile_showing_detail = True
            self._show_device(row.device)
            self._update_mobile_layout()
        elif (
            self.detected_list.get_selected_row() is None
            and self.offline_list.get_selected_row() is None
            and self.connected_list.get_selected_row() is None
        ):
            self._show_empty_state()

    def _clear_detail(self):
        parent = self.device_actions_button.get_parent()
        if parent is not None:
            parent.remove(self.device_actions_button)
        for child in self.detail.get_children():
            self.detail.remove(child)

    def _show_empty_state(self):
        self._clear_detail()
        self.current_device = None
        self._mobile_showing_detail = False
        self.device_actions_button.hide()

        placeholder = Gtk.Label(label="Select a device")
        placeholder.get_style_context().add_class("dim-label")
        placeholder.set_vexpand(True)
        placeholder.set_valign(Gtk.Align.CENTER)
        self.detail.pack_start(placeholder, True, True, 0)
        self.detail.show_all()
        self._update_mobile_layout()

    def _show_device(self, device, preserve_scroll=False):
        adjustment = self.detail_scroller.get_vadjustment()
        scroll_position = adjustment.get_value() if preserve_scroll else 0
        self._clear_detail()
        self.current_device = device
        is_connected = bool(device.get("connected"))
        self._set_device_actions(device)

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header_box.set_margin_top(24)
        header_box.set_hexpand(True)

        icon = Gtk.Image.new_from_icon_name(icon_for(device["type"]), Gtk.IconSize.DIALOG)
        header_box.pack_start(icon, False, False, 0)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        name_label = Gtk.Label(xalign=0)
        name_label.set_markup(
            "<span size='x-large'>{}</span>".format(GLib.markup_escape_text(device["name"]))
        )
        title_box.pack_start(name_label, False, False, 0)

        if is_connected:
            status_text = "Connected"
        elif device.get("pair_requested"):
            status_text = "Pairing request received"
        elif device.get("pair_pending"):
            status_text = "Pairing request sent"
        elif device.get("paired"):
            battery = device.get("battery")
            if isinstance(battery, (int, float)) and battery >= 0:
                status_text = "Offline · Battery {}% (last reported)".format(battery)
            else:
                status_text = "Offline"
        elif device.get("secure"):
            status_text = "Secure link established · Not paired"
        else:
            status_text = "Detected, not paired"
        status_label = Gtk.Label(label=status_text, xalign=0)
        status_label.get_style_context().add_class("dim-label")
        title_box.pack_start(status_label, False, False, 0)

        header_box.pack_start(title_box, True, True, 0)

        header_grid = Gtk.Grid()
        header_grid.set_column_spacing(12)
        header_grid.set_margin_start(24)
        header_grid.set_margin_end(24)
        header_grid.set_hexpand(True)
        header_grid.attach(header_box, 0, 0, 1, 1)
        self.device_actions_button.set_valign(Gtk.Align.CENTER)
        self.device_actions_button.set_halign(Gtk.Align.END)
        header_grid.attach(self.device_actions_button, 1, 0, 1, 1)
        self.detail.pack_start(header_grid, False, True, 0)

        if not is_connected:
            prompt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            prompt_box.set_vexpand(True)
            prompt_box.set_valign(Gtk.Align.CENTER)
            prompt_box.set_halign(Gtk.Align.CENTER)

            prompt_icon = Gtk.Image.new_from_icon_name(
                "dialog-question-symbolic", Gtk.IconSize.DIALOG
            )
            prompt_box.pack_start(prompt_icon, False, False, 0)

            incoming_request = bool(device.get("pair_requested"))
            if incoming_request:
                prompt_text = "{} is asking to pair with this PC.".format(device["name"])
            elif device.get("pair_pending"):
                prompt_text = "Pairing request sent to {}. Waiting for a response.".format(
                    device["name"]
                )
            elif device.get("paired"):
                prompt_text = "This device is paired but it is offline."
            elif device.get("secure"):
                prompt_text = "Do you want to pair with {}?".format(device["name"])
            else:
                prompt_text = "Establishing a secure connection with {}…".format(
                    device["name"]
                )
            prompt = Gtk.Label(label=prompt_text)
            prompt.get_style_context().add_class("dim-label")
            prompt_box.pack_start(prompt, False, False, 0)

            verification_code = device.get("verification_code")
            if verification_code:
                code_label = Gtk.Label(
                    label="Security code: {}".format(verification_code)
                )
                code_label.get_style_context().add_class("dim-label")
                prompt_box.pack_start(code_label, False, False, 0)

            response_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            if incoming_request:
                accept_button = Gtk.Button(label="Accept")
                accept_button.connect("clicked", self.on_pair_clicked)
                response_buttons.pack_start(accept_button, False, False, 0)

                deny_button = Gtk.Button(label="Deny")
                deny_button.connect("clicked", self.on_deny_pair_clicked)
                response_buttons.pack_start(deny_button, False, False, 0)
            elif device.get("secure") and not device.get("pair_pending"):
                pair_button = Gtk.Button(label="Pair")
                pair_button.connect("clicked", self.on_pair_clicked)
                response_buttons.pack_start(pair_button, False, False, 0)
            prompt_box.pack_start(response_buttons, False, False, 0)

            self.detail.pack_start(prompt_box, True, True, 0)
            self.detail.show_all()
            GLib.idle_add(self._restore_detail_scroll, scroll_position)
            self.device_actions_button.set_visible(bool(device.get("secure")))
            self._update_mobile_layout()
            return

        mpris_data = device.get("plugin_data", {}).get("mpris", {}).get("body", {})
        if mpris_data:
            self._add_media_controls(device["id"], mpris_data)

        battery_percent = device.get("battery")
        if isinstance(battery_percent, (int, float)) and battery_percent >= 0:
            battery_label = Gtk.Label(
                label="Battery · {}%{}".format(
                    battery_percent,
                    " · Charging" if device.get("isCharging") else "",
                ),
                xalign=0,
            )
            battery_label.set_margin_start(24)
            battery_label.set_margin_top(12)
            self.detail.pack_start(battery_label, False, False, 0)
            battery_bar = Gtk.ProgressBar()
            battery_bar.set_fraction(min(100, battery_percent) / 100.0)
            battery_bar.set_margin_start(24)
            battery_bar.set_margin_end(24)
            self.detail.pack_start(battery_bar, False, False, 0)

        available_plugins = device.get("available_plugins", [])
        if is_connected and available_plugins:
            self.detail.pack_start(Gtk.Separator(margin_top=18), False, False, 0)
            self.detail.pack_start(section_label("PLUGINS"), False, False, 0)
            plugin_list = Gtk.ListBox()
            plugin_list.set_selection_mode(Gtk.SelectionMode.NONE)
            plugin_list.get_style_context().add_class(Gtk.STYLE_CLASS_VIEW)
            plugin_search = Gtk.SearchEntry()
            plugin_search.set_placeholder_text("Search plugins")
            plugin_search.set_size_request(-1, 32)
            plugin_search.set_margin_start(16)
            plugin_search.set_margin_end(16)
            plugin_search.set_margin_top(4)
            plugin_search.set_margin_bottom(4)
            plugin_list.set_filter_func(self._filter_plugin_row, plugin_search)
            plugin_search.connect(
                "search-changed",
                lambda _entry, target=plugin_list: target.invalidate_filter(),
            )
            for plugin in available_plugins:
                plugin_list.add(self._plugin_row(device["id"], plugin))
            no_matches = Gtk.Label(label="No matching plugins")
            no_matches.get_style_context().add_class("dim-label")
            no_matches.set_margin_top(16)
            no_matches.set_margin_bottom(16)
            plugin_list.set_placeholder(no_matches)
            plugin_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            plugin_area.pack_start(plugin_search, False, False, 0)
            plugin_area.pack_start(plugin_list, False, True, 0)
            self.detail.pack_start(plugin_area, False, True, 0)

        if is_connected and device.get("last_ping"):
            last_ping = Gtk.Label(label="Last ping: {}".format(device["last_ping"]), xalign=0)
            last_ping.set_margin_start(24)
            last_ping.set_margin_top(8)
            self.detail.pack_start(last_ping, False, False, 0)

        self.detail.show_all()
        GLib.idle_add(self._restore_detail_scroll, scroll_position)
        self.device_actions_button.set_visible(bool(device.get("secure")))
        self._update_mobile_layout()

    def _restore_detail_scroll(self, position):
        adjustment = self.detail_scroller.get_vadjustment()
        maximum = max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(min(position, maximum))
        return GLib.SOURCE_REMOVE

    def _add_media_controls(self, device_id, media):
        player = media.get("player")
        if not player:
            players = media.get("playerList", [])
            player = players[0] if players else None
        title = media.get("title") or media.get("nowPlaying") or "No track information"
        artist = media.get("artist") or media.get("album")

        frame = Gtk.Frame(label="Media")
        frame.set_margin_start(24)
        frame.set_margin_end(24)
        frame.set_margin_top(12)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(8)
        content.set_margin_bottom(8)
        title_label = Gtk.Label(label=title, xalign=0)
        content.pack_start(title_label, False, False, 0)
        if artist:
            artist_label = Gtk.Label(label=artist, xalign=0)
            artist_label.get_style_context().add_class("dim-label")
            content.pack_start(artist_label, False, False, 0)

        if player:
            controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            controls.set_halign(Gtk.Align.START)
            buttons = (
                ("media-skip-backward-symbolic", "Previous", "Previous track"),
                (
                    "media-playback-pause-symbolic" if media.get("isPlaying") else "media-playback-start-symbolic",
                    "PlayPause",
                    "Pause" if media.get("isPlaying") else "Play",
                ),
                ("media-skip-forward-symbolic", "Next", "Next track"),
            )
            for icon_name, action, tooltip in buttons:
                button = Gtk.Button()
                button.set_image(
                    Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
                )
                button.set_tooltip_text(tooltip)
                button.connect(
                    "clicked",
                    lambda _button, selected_player=player, selected_action=action:
                        self.discovery.send_plugin_packet(
                            "mpris",
                            device_id,
                            "kdeconnect.mpris.request",
                            {"player": selected_player, "action": selected_action},
                        ),
                )
                controls.pack_start(button, False, False, 0)
            content.pack_start(controls, False, False, 0)

        frame.add(content)
        self.detail.pack_start(frame, False, False, 0)

    def _plugin_row(self, device_id, plugin):
        row = Gtk.ListBoxRow()
        row.search_text = "{} {}".format(
            plugin.get("name", ""), plugin.get("description", "")
        ).casefold()
        layout = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        layout.set_margin_start(8)
        layout.set_margin_end(8)
        layout.set_margin_top(5)
        layout.set_margin_bottom(5)

        icon_name = plugin.get("icon") or "application-x-addon-symbolic"
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DND)
        icon.set_size_request(24, 24)
        layout.pack_start(icon, False, False, 0)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(label=plugin["name"], xalign=0)
        labels.pack_start(name, False, False, 0)
        description = plugin.get("description")
        if description:
            hint = Gtk.Label(label=description, xalign=0)
            hint.get_style_context().add_class("dim-label")
            hint.set_line_wrap(True)
            labels.pack_start(hint, False, False, 0)
        layout.pack_start(labels, True, True, 0)

        if plugin.get("configurable"):
            configure = Gtk.Button()
            configure.set_image(
                Gtk.Image.new_from_icon_name(
                    "preferences-system-symbolic", Gtk.IconSize.BUTTON
                )
            )
            configure.set_relief(Gtk.ReliefStyle.NONE)
            configure.set_tooltip_text("Configure {}".format(plugin["name"]))
            configure.connect(
                "clicked",
                lambda _button, selected_id=device_id, plugin_id=plugin["id"]:
                    self._configure_plugin(selected_id, plugin_id),
            )
            layout.pack_start(configure, False, False, 0)

        toggle = Gtk.Switch()
        toggle.set_active(bool(plugin.get("enabled", True)))
        toggle.set_valign(Gtk.Align.CENTER)
        toggle.set_tooltip_text("Enable or disable {}".format(plugin["name"]))
        toggle.connect(
            "state-set", self._on_plugin_toggled, device_id, plugin["id"]
        )
        layout.pack_start(toggle, False, False, 0)

        row.add(layout)
        return row

    @staticmethod
    def _filter_plugin_row(row, search_entry):
        query = search_entry.get_text().strip().casefold()
        return not query or query in row.search_text

    def _on_plugin_toggled(self, widget, state, device_id, plugin_id):
        if not self.discovery.set_plugin_enabled(device_id, plugin_id, state):
            return True
        return False

    def _configure_plugin(self, device_id, plugin_id):
        device = next(
            (item for item in self.discovery.devices() if item["id"] == device_id),
            None,
        )
        plugin = next(
            (
                item for item in (device or {}).get("available_plugins", [])
                if item["id"] == plugin_id
            ),
            None,
        )
        if not plugin:
            return

        dialog = Gtk.Dialog(
            title="Configure {}".format(plugin["name"]),
            transient_for=self,
            modal=True,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.ACCEPT)
        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        grid.set_border_width(12)
        settings = self.discovery.plugin_settings(device_id, plugin_id)
        widgets = {}
        for row_number, field in enumerate(plugin.get("settings_schema", [])):
            label = Gtk.Label(label=field["label"], xalign=0)
            grid.attach(label, 0, row_number, 1, 1)
            field_type = field.get("type")
            if field_type == "bool":
                control = Gtk.Switch()
                control.set_active(bool(settings.get(field["key"], field.get("default", False))))
                control.set_halign(Gtk.Align.END)
            elif field_type == "int":
                control = Gtk.SpinButton.new_with_range(
                    field.get("minimum", 0), field.get("maximum", 100), 1
                )
                control.set_value(settings.get(field["key"], field.get("default", 0)))
            else:
                control = Gtk.Entry()
                control.set_text(str(settings.get(field["key"], field.get("default", ""))))
            grid.attach(control, 1, row_number, 1, 1)
            widgets[field["key"]] = (field_type, control)
        dialog.get_content_area().add(grid)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            values = {}
            for key, (field_type, control) in widgets.items():
                if field_type == "bool":
                    values[key] = control.get_active()
                elif field_type == "int":
                    values[key] = control.get_value_as_int()
                else:
                    values[key] = control.get_text()
            self.discovery.set_plugin_settings(device_id, plugin_id, values)
        dialog.destroy()

    def _set_device_actions(self, device):
        menu = Gtk.Menu()

        def add_action(label, callback):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            menu.append(item)

        device_id = device["id"]
        enabled_plugins = device.get("plugins", [])
        if device.get("paired"):
            add_action(
                "Unpair",
                lambda item, selected_id=device_id: self.on_unpair_clicked(item, selected_id),
            )
        if device.get("paired") and "Ping" in enabled_plugins:
            add_action(
                "Send ping",
                lambda item, selected_id=device_id: self.on_ping_clicked(item, selected_id),
            )
        if device.get("secure"):
            add_action(
                "Encryption info",
                lambda _item, selected_device=device: self._show_encryption_info(selected_device),
            )
        if device.get("paired") and "Ring" in enabled_plugins:
            add_action(
                "Ring",
                lambda item, selected_id=device_id: self.on_ring_clicked(item, selected_id),
            )

        menu.show_all()
        self.device_actions_button.set_popup(menu)

    def _show_encryption_info(self, device):
        encryption = device.get("encryption", {})
        protocol = encryption.get("protocol") or "Unknown TLS version"
        cipher = encryption.get("cipher") or "Unknown cipher suite"
        fingerprint = encryption.get("fingerprint") or "Unavailable"
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Encrypted connection",
        )
        dialog.format_secondary_text(
            "Device: {}\nProtocol: {}\nCipher: {}\nCertificate SHA-256: {}".format(
                device["name"], protocol, cipher, fingerprint
            )
        )
        dialog.run()
        dialog.destroy()

    def on_unpair_clicked(self, button, device_id=None):
        device = self.current_device
        if device_id:
            device = next(
                (item for item in self.discovery.devices() if item["id"] == device_id),
                device,
            )
        if device:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text="Unpair from {}?".format(device["name"]),
            )
            dialog.format_secondary_text(
                "This removes the trusted connection to this device."
            )
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dialog.add_button("Unpair", Gtk.ResponseType.ACCEPT)
            dialog.set_default_response(Gtk.ResponseType.CANCEL)
            response = dialog.run()
            dialog.destroy()
            if response == Gtk.ResponseType.ACCEPT and not self.discovery.unpair(device["id"]):
                self._show_pairing_error()

    def on_pair_clicked(self, button):
        if self.current_device:
            device_id = self.current_device["id"]
            if self.current_device.get("pair_requested"):
                success = self.discovery.respond_pair(device_id, True)
            else:
                success = self.discovery.request_pair(device_id)
            if not success:
                self._show_pairing_error()

    def on_deny_pair_clicked(self, button):
        if self.current_device:
            if not self.discovery.respond_pair(self.current_device["id"], False):
                self._show_pairing_error()

    def _show_pairing_error(self):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text="Could not exchange the pairing request",
        )
        dialog.format_secondary_text(
            "The secure device connection is not available. Check that both devices are on the same network and that the KDE Connect ports are allowed through the firewall."
        )
        dialog.run()
        dialog.destroy()

    def on_ping_clicked(self, button, device_id=None):
        device = self.current_device
        if device_id:
            device = next(
                (item for item in self.discovery.devices() if item["id"] == device_id),
                device,
            )
        if device:
            if not self.discovery.send_ping(device["id"]):
                dialog = Gtk.MessageDialog(
                    transient_for=self,
                    modal=True,
                    message_type=Gtk.MessageType.WARNING,
                    buttons=Gtk.ButtonsType.OK,
                    text="Could not send ping",
                )
                dialog.format_secondary_text(
                    "The secure connection is no longer active or the device does not support Ping."
                )
                dialog.run()
                dialog.destroy()

    def on_ring_clicked(self, button, device_id=None):
        device = self.current_device
        if device_id:
            device = next(
                (item for item in self.discovery.devices() if item["id"] == device_id),
                device,
            )
        if device and not self.discovery.ring_device(device["id"]):
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Could not ring device",
            )
            dialog.format_secondary_text(
                "The secure connection is unavailable or the device does not support Ring."
            )
            dialog.run()
            dialog.destroy()


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
        about.connect("response", lambda dialog, _response: dialog.destroy())
        about.present()


def main():
    app = SatelliteApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
