"""What the presenter pointer looks like, and drawing it.

Shared by the overlay over the slides and the live preview in the
Presenter settings, so both always match. Only needs cairo (and GdkPixbuf
for a custom image).
"""

import math
import time

import cairo

SIZES = {"small": 1 / 150, "medium": 1 / 80, "large": 1 / 45}            # dot diameter / screen width
SPOTLIGHT_SIZES = {"small": 0.05, "medium": 0.08, "large": 0.12}          # hole radius / screen width
OUTLINES = {"none": 0.0, "thin": 1.5, "thick": 3.5}
HIDE_AFTER = {"0.5": 500, "2": 2000, "5": 5000, "never": None}           # milliseconds
IMAGE_SCALE = 3.0      # an image pointer is this many times the dot's size
TRAIL_SECONDS = 0.35   # how long the laser trail lingers


def parse_color(text, fallback):
    """'#rrggbb' to (r, g, b) floats."""
    text = (text or "").strip().lstrip("#")
    if len(text) != 6:
        text = fallback.lstrip("#")
    try:
        return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return parse_color(fallback, "#e01b24")


def _clamp_percent(value, default, low, high):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value)) / 100


def pointer_look(settings):
    """Turn the Presenter settings into everything the drawing needs."""
    style = settings.get("style", "dot")
    if style == "image" and not settings.get("image"):
        style = "dot"  # no picture chosen yet
    return {
        "style": style,
        "color": parse_color(settings.get("color"), "#e01b24"),
        "opacity": _clamp_percent(settings.get("opacity"), 80, 10, 100),
        "size": SIZES.get(settings.get("size"), SIZES["small"]),
        "outline": OUTLINES.get(settings.get("outline"), OUTLINES["thin"]),
        "outline_color": parse_color(settings.get("outline_color"), "#ffffff"),
        "image": settings.get("image") or "",
        "darkness": _clamp_percent(settings.get("spotlight_darkness"), 60, 10, 95),
        "spotlight": SPOTLIGHT_SIZES.get(settings.get("spotlight_size"), SPOTLIGHT_SIZES["medium"]),
        "ring": bool(settings.get("spotlight_ring", False)),
        "trail": bool(settings.get("trail", False)),
        "hide_ms": HIDE_AFTER.get(str(settings.get("hide_after", "0.5")), 500),
    }


class Trail:
    """Recent pointer positions, for the laser trail."""

    def __init__(self, seconds=TRAIL_SECONDS):
        self.seconds = seconds
        self.points = []  # (x, y, time)

    def add(self, x, y, now=None):
        now = time.monotonic() if now is None else now
        self.points.append((x, y, now))
        self.prune(now)

    def prune(self, now=None):
        now = time.monotonic() if now is None else now
        self.points = [p for p in self.points if now - p[2] <= self.seconds]

    def faded(self, now=None):
        """(x, y, strength) with strength 1 (new) to 0 (about to vanish)."""
        now = time.monotonic() if now is None else now
        return [(x, y, 1 - (now - t) / self.seconds) for x, y, t in self.points if now - t <= self.seconds]

    def clear(self):
        self.points = []


_image_cache = {}


def load_image(path, size):
    """The custom pointer picture at about ``size`` pixels, or None."""
    key = (path, int(size))
    if key not in _image_cache:
        try:
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf, GLib
        except (ImportError, ValueError):
            return None
        try:
            _image_cache[key] = GdkPixbuf.Pixbuf.new_from_file_at_size(path, int(size), int(size))
        except GLib.Error:
            _image_cache[key] = None
    return _image_cache[key]


def draw_pointer(cr, width, height, x, y, look, trail=(), screen_width=None):
    """Draw the pointer at (x, y), fractions of the area. The area is assumed clear.

    ``screen_width`` sizes the dot, picture and trail as on a screen that wide
    (the settings preview shows their real size); the spotlight always fits
    the area it is drawn on.
    """
    px, py = width * x, height * y
    red, green, blue = look["color"]
    opacity = look["opacity"]
    if look["style"] == "spotlight":
        radius = width * look["spotlight"]
        cr.set_source_rgba(0, 0, 0, look["darkness"])
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)  # the circle is a hole
        cr.rectangle(0, 0, width, height)
        cr.arc_negative(px, py, radius, 2 * math.pi, 0)
        cr.fill()
        if look["ring"]:
            cr.arc(px, py, radius, 0, 2 * math.pi)
            cr.set_source_rgba(red, green, blue, opacity)
            cr.set_line_width(max(2.0, width / 400))
            cr.stroke()
        return

    radius = max(4.0, (screen_width or width) * look["size"] / 2)
    # The laser trail: a streak through recent positions, thinner and fainter
    # the older it is.
    points = [(width * tx, height * ty, strength) for tx, ty, strength in trail if strength > 0]
    if points:
        points.append((px, py, 1.0))
        # Drawn as one layer so overlapping segments don't stack up into beads.
        cr.push_group()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgb(red, green, blue)
        for (x0, y0, s0), (x1, y1, s1) in zip(points, points[1:]):
            cr.move_to(x0, y0)
            cr.line_to(x1, y1)
            cr.set_line_width(max(1.0, 1.6 * radius * (s0 + s1) / 2))
            cr.stroke()
        cr.pop_group_to_source()
        cr.paint_with_alpha(opacity * 0.55)

    if look["style"] == "image":
        image = load_image(look["image"], radius * 2 * IMAGE_SCALE)
        if image is not None:
            import gi
            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk
            Gdk.cairo_set_source_pixbuf(cr, image, px - image.get_width() / 2, py - image.get_height() / 2)
            cr.paint_with_alpha(opacity)
            return
    cr.arc(px, py, radius, 0, 2 * math.pi)
    cr.set_source_rgba(red, green, blue, opacity)
    if look["outline"]:
        cr.fill_preserve()
        outline_red, outline_green, outline_blue = look["outline_color"]
        cr.set_source_rgba(outline_red, outline_green, outline_blue, min(1.0, opacity + 0.1))
        cr.set_line_width(look["outline"])
        cr.stroke()
    else:
        cr.fill()
