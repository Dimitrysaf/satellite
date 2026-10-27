"""The main buttons on a device page, supplied by the device's plugins."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class ActionBar(Gtk.FlowBox):
    def __init__(self, actions, on_activate):
        super().__init__()
        self.set_selection_mode(Gtk.SelectionMode.NONE)
        self.set_homogeneous(True)
        self.set_min_children_per_line(2)
        self.set_max_children_per_line(6)
        self.set_column_spacing(8)
        self.set_row_spacing(8)
        for side in ("start", "end"):
            getattr(self, "set_margin_" + side)(24)
        self.set_margin_top(16)

        for _plugin_id, action in actions:
            button = Gtk.Button()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.set_border_width(6)
            box.pack_start(Gtk.Image.new_from_icon_name(action.icon, Gtk.IconSize.LARGE_TOOLBAR), False, False, 0)
            box.pack_start(Gtk.Label(label=action.label), False, False, 0)
            button.add(box)
            button.set_tooltip_text(action.tooltip)
            button.connect("clicked", lambda _b, a=action: on_activate(a))
            self.add(button)
