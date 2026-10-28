"""Round contact pictures: the contact's photo, or initials on a colour."""

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

# Muted colours that read well under white text in light and dark themes.
COLORS = ("#3584e4", "#2190a4", "#3a944a", "#c88800", "#ed5b00", "#e62d42", "#d56199", "#9141ac", "#6f8396")


def initials(name):
    words = [w for w in (name or "").replace("+", " ").split() if w[:1].isalpha()]
    if not words:
        return ""
    return (words[0][0] + (words[-1][0] if len(words) > 1 else "")).upper()


def color_for(name):
    return COLORS[sum(map(ord, name or "")) % len(COLORS)]


def load_photo(data, size):
    if not data:
        return None
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
    except GLib.Error:
        return None
    if pixbuf is None:
        return None
    side = min(pixbuf.get_width(), pixbuf.get_height())
    square = pixbuf.new_subpixbuf(
        (pixbuf.get_width() - side) // 2, (pixbuf.get_height() - side) // 2, side, side)
    return square.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)


class Avatar(Gtk.DrawingArea):
    def __init__(self, name, photo=None, size=40, icon="avatar-default-symbolic"):
        super().__init__()
        self._size = size
        self._text = initials(name)
        self._color = Gdk.RGBA()
        self._color.parse(color_for(name))
        self._icon = icon
        self._pixbuf = load_photo(photo, size * self.get_scale_factor())
        self.set_size_request(size, size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.connect("draw", self._on_draw)

    def _on_draw(self, widget, cr):
        size = self._size
        cr.arc(size / 2, size / 2, size / 2, 0, 6.2832)
        cr.clip()
        if self._pixbuf is not None:
            scale = widget.get_scale_factor()
            cr.scale(1 / scale, 1 / scale)
            Gdk.cairo_set_source_pixbuf(cr, self._pixbuf, 0, 0)
            cr.paint()
            return False
        Gdk.cairo_set_source_rgba(cr, self._color)
        cr.paint()
        cr.set_source_rgb(1, 1, 1)
        if self._text:
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(size * 0.38)
            extents = cr.text_extents(self._text)
            cr.move_to(size / 2 - extents.width / 2 - extents.x_bearing,
                       size / 2 - extents.height / 2 - extents.y_bearing)
            cr.show_text(self._text)
        else:
            info = Gtk.IconTheme.get_default().lookup_icon(self._icon, int(size * 0.6), 0)
            if info is None:
                return False
            white = Gdk.RGBA()
            white.parse("#ffffff")
            try:
                icon = info.load_symbolic(white, None, None, None)[0]
            except GLib.Error:
                return False
            Gdk.cairo_set_source_pixbuf(cr, icon, (size - icon.get_width()) / 2, (size - icon.get_height()) / 2)
            cr.paint()
        return False
