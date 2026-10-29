"""Small helpers shared by several UI modules."""

import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk
from satellite.i18n import _

ICONS = {
    "phone": "phone-symbolic",
    "tablet": "tablet-symbolic",
    "laptop": "computer-symbolic",
    "desktop": "computer-symbolic",
}


def icon_for(device_type):
    return ICONS.get(device_type, "network-wireless-symbolic")


def load_satellite_logo(size=64):
    candidates = (
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "satellite.svg"),
        "/usr/share/icons/hicolor/scalable/apps/satellite.svg",
    )
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
        except GLib.Error:
            continue
    try:
        return Gtk.IconTheme.get_default().load_icon("satellite", size, 0)
    except GLib.Error:
        return None


def section_label(text):
    label = Gtk.Label(xalign=0)
    label.set_margin_top(10)
    label.set_margin_start(10)
    label.set_margin_bottom(4)
    label.set_markup("<b>{}</b>".format(GLib.markup_escape_text(text)))
    label.get_style_context().add_class("dim-label")
    return label


def format_bytes(amount):
    value = float(amount)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return "{} {}".format(int(value) if unit == "B" else "{:.1f}".format(value), unit)
        value /= 1024


def battery_percent(device):
    value = device.get("battery")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def short_status(device):
    """One-line status for the device list."""
    battery = battery_percent(device)
    suffix = " · {}%".format(battery) if battery is not None else ""
    if device.get("connected"):
        return _("Connected") + suffix
    if device.get("pair_requested"):
        return _("Pairing request")
    if device.get("pair_pending"):
        return _("Pair requested")
    if device.get("paired"):
        return _("Offline") + suffix
    if device.get("secure"):
        return _("Not paired")
    return _("Not reachable")


def long_status(device):
    """Descriptive status for the device page header."""
    battery = battery_percent(device)
    if device.get("connected"):
        return _("Connected")
    if device.get("pair_requested"):
        return _("Pairing request")
    if device.get("pair_pending"):
        return _("Pair requested")
    if device.get("paired"):
        if battery is not None:
            return _("Offline · Battery {}% (last reported)").format(battery)
        return _("Offline")
    if device.get("secure"):
        return _("Not paired")
    return _("Not reachable")
