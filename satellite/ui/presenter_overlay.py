"""A click-through, fullscreen overlay that draws the presenter pointer.

Like KDE Connect's presenter, it is a transparent window above everything
else with an empty input region, so clicks still reach the presentation.

It must stay above a fullscreen, focused slideshow:

* X11: an override-redirect popup, which window managers never restack.
* Wayland: the layer-shell "overlay" layer when the compositor offers it
  (gtk-layer-shell); otherwise a fullscreen keep-above window, best effort.
"""

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from satellite.presenter_look import Trail, draw_pointer, pointer_look
from satellite.i18n import _

FRAME_MS = 30  # redraws while the laser trail fades


def _layer_shell():
    """The GtkLayerShell module if this Wayland compositor supports it."""
    try:
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell
    except (ImportError, ValueError):
        return None
    return GtkLayerShell if GtkLayerShell.is_supported() else None


def _is_wayland(display):
    return type(display).__name__.startswith("Wayland")


NO_COMPOSITING = "no-compositing"
NO_LAYER_SHELL = "no-layer-shell"
LIMITATIONS = {
    NO_COMPOSITING: _("This desktop can't draw see-through windows (compositing is off), so the "
                    "pointer can't be drawn over the slides."),
    NO_LAYER_SHELL: _("On this Wayland desktop the pointer may end up behind fullscreen slides: "
                    "the compositor doesn't offer the overlay layer (layer-shell)."),
}


def overlay_limitation():
    """Why the drawn pointer may not show over the slides here, or None."""
    display = Gdk.Display.get_default()
    if display is None:
        return None
    screen = display.get_default_screen()
    if screen.get_rgba_visual() is None or not screen.is_composited():
        return NO_COMPOSITING
    if _is_wayland(display) and _layer_shell() is None:
        return NO_LAYER_SHELL
    return None


class PresenterOverlay(Gtk.Window):
    def __init__(self):
        wayland = _is_wayland(Gdk.Display.get_default())
        self._layer_shell = _layer_shell() if wayland else None
        # On X11 a popup is override-redirect: always on top, never managed.
        kind = Gtk.WindowType.TOPLEVEL if wayland else Gtk.WindowType.POPUP
        super().__init__(type=kind, title="Satellite presenter")
        self._wayland = wayland
        self._x = self._y = 0.5
        self._look = pointer_look({})
        self._trail = Trail()
        self._fade_source = 0
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        self.supported = visual is not None and screen.is_composited()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        if self._layer_shell is not None:
            shell = self._layer_shell
            shell.init_for_window(self)
            shell.set_layer(self, shell.Layer.OVERLAY)
            for edge in (shell.Edge.TOP, shell.Edge.BOTTOM, shell.Edge.LEFT, shell.Edge.RIGHT):
                shell.set_anchor(self, edge, True)
            shell.set_exclusive_zone(self, -1)
            shell.set_keyboard_interactivity(self, False)
        self.connect("draw", self._on_draw)
        self.connect("realize", self._on_realize)
        self._hide_source = 0

    @staticmethod
    def _on_realize(window):
        # Let every click and key go through to the presentation underneath.
        window.input_shape_combine_region(cairo.Region())

    def point(self, x, y, settings):
        """Show the pointer at (x, y), fractions of the screen size."""
        self._x, self._y = x, y
        self._look = pointer_look(settings)
        if self._look["trail"]:
            self._trail.add(x, y)
            if not self._fade_source:
                self._fade_source = GLib.timeout_add(FRAME_MS, self._fade)
        if not self.get_visible():
            self._cover_monitor()
            self.show()
        self.queue_draw()
        if self._hide_source:
            GLib.source_remove(self._hide_source)
            self._hide_source = 0
        if self._look["hide_ms"] is not None:  # None: stays until the presentation stops
            self._hide_source = GLib.timeout_add(self._look["hide_ms"], self.stop)

    def _fade(self):
        self._trail.prune()
        self.queue_draw()
        if self._trail.points and self.get_visible():
            return True
        self._fade_source = 0
        return False

    def _cover_monitor(self):
        """Cover the monitor the mouse pointer is on (or the primary one)."""
        display = Gdk.Display.get_default()
        monitor = None
        seat = display.get_default_seat()
        if seat is not None and not self._wayland:
            _screen, x, y = seat.get_pointer().get_position()
            monitor = display.get_monitor_at_point(x, y)
        monitor = monitor or display.get_primary_monitor() or display.get_monitor(0)
        if self._layer_shell is not None:
            self._layer_shell.set_monitor(self, monitor)
        elif self._wayland:
            self.fullscreen_on_monitor(self.get_screen(), _monitor_index(display, monitor))
        else:
            area = monitor.get_geometry()
            self.move(area.x, area.y)
            self.resize(area.width, area.height)

    def stop(self):
        if self._hide_source:
            GLib.source_remove(self._hide_source)
        self._hide_source = 0
        self._trail.clear()
        self.hide()
        return False

    def _on_draw(self, _widget, cr):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        draw_pointer(cr, width, height, self._x, self._y, self._look,
                     self._trail.faded() if self._look["trail"] else ())
        return False

def _monitor_index(display, monitor):
    for index in range(display.get_n_monitors()):
        if display.get_monitor(index) == monitor:
            return index
    return 0
