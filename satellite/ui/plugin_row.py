"""One plugin in a device's plugin list: icon, name, ⚙ and an on/off switch."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
from satellite.i18n import _


class PluginRow(Gtk.ListBoxRow):
    def __init__(self, plugin, on_toggled, on_configure):
        super().__init__()
        self.search_text = "{} {}".format(
            plugin.get("name", ""), plugin.get("description", "")).casefold()
        layout = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        layout.set_margin_start(8)
        layout.set_margin_end(8)
        layout.set_margin_top(5)
        layout.set_margin_bottom(5)

        icon = Gtk.Image.new_from_icon_name(
            plugin.get("icon") or "application-x-addon-symbolic", Gtk.IconSize.DND)
        icon.set_size_request(24, 24)
        layout.pack_start(icon, False, False, 0)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        labels.pack_start(Gtk.Label(label=plugin["name"], xalign=0), False, False, 0)
        if plugin.get("description"):
            hint = Gtk.Label(label=plugin["description"], xalign=0)
            hint.get_style_context().add_class("dim-label")
            hint.set_line_wrap(True)
            labels.pack_start(hint, False, False, 0)
        layout.pack_start(labels, True, True, 0)

        if plugin.get("configurable"):
            configure = Gtk.Button.new_from_icon_name(
                "preferences-system-symbolic", Gtk.IconSize.BUTTON)
            configure.set_relief(Gtk.ReliefStyle.NONE)
            configure.set_valign(Gtk.Align.CENTER)
            configure.set_tooltip_text(_("Configure {}").format(plugin["name"]))
            configure.connect("clicked", lambda _b: on_configure(plugin["id"]))
            layout.pack_start(configure, False, False, 0)

        toggle = Gtk.Switch()
        toggle.set_active(bool(plugin.get("enabled", True)))
        toggle.set_valign(Gtk.Align.CENTER)
        toggle.set_tooltip_text(_("Enable or disable {}").format(plugin["name"]))
        toggle.connect("state-set", lambda _w, state: not on_toggled(plugin["id"], state))
        layout.pack_start(toggle, False, False, 0)
        self.add(layout)
