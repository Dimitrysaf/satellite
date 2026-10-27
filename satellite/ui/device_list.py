"""The sidebar: connected, offline and newly detected devices."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk

from satellite.ui.device_row import DeviceRow
from satellite.ui.empty_state import EmptyState
from satellite.ui.widgets import section_label


class DeviceList(Gtk.ScrolledWindow):
    __gsignals__ = {
        "device-selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "selection-cleared": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, on_refresh=None):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._updating = False
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.connected_list = self._make_list()
        self.offline_list = self._make_list()
        self.detected_list = self._make_list()
        self._lists = (self.connected_list, self.offline_list, self.detected_list)

        self.connected_heading = section_label("CONNECTED")
        self.offline_heading = section_label("OFFLINE")
        self.detected_heading = section_label("DETECTED")
        self.connected_separator = Gtk.Separator(margin_top=8, margin_bottom=4)
        self.offline_separator = Gtk.Separator(margin_top=8, margin_bottom=4)

        for widget in (
            self.connected_heading, self.connected_list, self.connected_separator,
            self.offline_heading, self.offline_list, self.offline_separator,
            self.detected_heading, self.detected_list,
        ):
            inner.pack_start(widget, False, False, 0)

        self.empty = EmptyState(
            "network-wireless-symbolic", "No devices found",
            "Open KDE Connect on your phone and connect it to the same network as this computer.",
            "Search again", on_refresh,
        )
        inner.pack_start(self.empty, True, True, 0)
        self.add(inner)

    def _make_list(self):
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        listbox.connect("row-selected", self._on_row_selected)
        return listbox

    @property
    def selected_id(self):
        for listbox in self._lists:
            row = listbox.get_selected_row()
            if row is not None:
                return row.device["id"]
        return None

    def _on_row_selected(self, listbox, row):
        if self._updating:
            return
        if row is not None:
            for other in self._lists:
                if other is not listbox:
                    other.unselect_all()
            self.emit("device-selected", row.device)
        elif self.selected_id is None:
            self.emit("selection-cleared")

    def clear_selection(self):
        for listbox in self._lists:
            listbox.unselect_all()

    def select(self, device_id):
        for listbox in self._lists:
            for row in listbox.get_children():
                if row.device["id"] == device_id:
                    listbox.select_row(row)
                    return True
        return False

    def update(self, devices):
        """Move, add, update and remove rows without disturbing the selection."""
        selected_id = self.selected_id
        old_rows = {}
        for listbox in self._lists:
            for row in listbox.get_children():
                old_rows[row.device["id"]] = row

        self._updating = True
        try:
            for device in devices:
                if device.get("connected"):
                    target = self.connected_list
                elif device.get("paired"):
                    target = self.offline_list
                else:
                    target = self.detected_list
                row = old_rows.pop(device["id"], None)
                if row is None:
                    row = DeviceRow(device)
                    target.add(row)
                else:
                    row.update(device)
                    if row.get_parent() is not target:
                        row.get_parent().remove(row)
                        target.add(row)
                row.show_all()
                if device["id"] == selected_id and row.get_parent().get_selected_row() is not row:
                    row.get_parent().select_row(row)
            for row in old_rows.values():
                row.get_parent().remove(row)
        finally:
            self._updating = False

        connected = any(d.get("connected") for d in devices)
        offline = any(d.get("paired") and not d.get("connected") for d in devices)
        detected = any(not d.get("connected") and not d.get("paired") for d in devices)
        for heading, listbox, visible in (
            (self.connected_heading, self.connected_list, connected),
            (self.offline_heading, self.offline_list, offline),
            (self.detected_heading, self.detected_list, detected),
        ):
            heading.set_visible(visible)
            listbox.set_visible(visible)
        self.connected_separator.set_visible(connected and (offline or detected))
        self.offline_separator.set_visible(offline and detected)
        self.empty.set_visible(not (connected or offline or detected))
