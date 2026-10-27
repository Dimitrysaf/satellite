"""A small always-on-top card showing one file transfer."""

import os

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from satellite.ui.widgets import format_bytes


class TransferPopup(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("File transfer")
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)

        frame = Gtk.Frame()
        for side in ("start", "end", "top", "bottom"):
            getattr(frame, "set_margin_" + side)(12)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_border_width(12)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._title = Gtk.Label(xalign=0)
        self._title.set_ellipsize(3)  # Pango.EllipsizeMode.END
        self._title.set_max_width_chars(36)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.set_tooltip_text("Hide transfer progress")
        close.connect("clicked", lambda _b: self.hide())
        heading.pack_start(self._title, True, True, 0)
        heading.pack_end(close, False, False, 0)
        self._details = Gtk.Label(xalign=0)
        self._details.get_style_context().add_class("dim-label")
        self._bar = Gtk.ProgressBar()
        self._bar.set_show_text(True)
        for widget in (heading, self._details, self._bar):
            content.pack_start(widget, False, False, 0)
        frame.add(content)
        self.add(frame)

    def update(self, progress):
        sending = progress.get("direction") == "send"
        percent = max(0, min(100, progress.get("percent", 0)))
        self._title.set_markup("<b>{}</b>".format(GLib.markup_escape_text(progress["filename"])))
        if progress.get("error"):
            details = "Transfer failed · {}".format(progress["error"])
        elif progress.get("finished"):
            details = "Sent to {}".format(progress["device_name"]) if sending else \
                "Saved to {}".format(os.path.dirname(progress["path"] or ""))
        else:
            details = "{} {} · {} of {} · {}%".format(
                "To" if sending else "From", progress["device_name"],
                format_bytes(progress["received"]), format_bytes(progress["total"]), percent)
        self._details.set_text(details)
        self._bar.set_fraction(percent / 100.0)
        self._bar.set_text("{}%".format(percent))
        was_visible = self.get_visible()
        self.show_all()
        if not was_visible:
            self.present()
