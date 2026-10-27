"""A friendly placeholder with an icon, a title, some help and an optional button."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class EmptyState(Gtk.Box):
    def __init__(self, icon_name, title, text, button_label=None, on_button=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_vexpand(True)
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)

        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
        image.set_pixel_size(64)
        image.get_style_context().add_class("dim-label")
        self.pack_start(image, False, False, 0)

        heading = Gtk.Label()
        heading.set_markup("<span size='large' weight='bold'>{}</span>".format(
            title.replace("&", "&amp;").replace("<", "&lt;")))
        self.pack_start(heading, False, False, 0)

        body = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER)
        body.set_max_width_chars(36)
        body.get_style_context().add_class("dim-label")
        self.pack_start(body, False, False, 0)

        if button_label and on_button:
            button = Gtk.Button(label=button_label)
            button.set_halign(Gtk.Align.CENTER)
            button.set_margin_top(8)
            button.connect("clicked", lambda _b: on_button())
            self.pack_start(button, False, False, 0)
