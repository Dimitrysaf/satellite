"""Hand this computer's mouse and keyboard to another one across a screen edge (X11).

While the pointer is on this computer, a light timer watches for it to reach
an edge that leads to another device. Then a tiny window grabs the pointer
and keyboard, hides the pointer and keeps putting it back in the middle of
the screen, so every movement can be measured and sent. The other computer
says when the pointer comes back, and how far past the edge it went.
"""

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from satellite.plugins.remoteinput import key_body

POLL_MS = 20  # how often the pointer is checked against the edges
SCROLL_STEP = 15  # per wheel notch, as KDE Connect sends it
BUTTON_DOWN = (Gdk.ModifierType.BUTTON1_MASK | Gdk.ModifierType.BUTTON2_MASK
               | Gdk.ModifierType.BUTTON3_MASK)


def is_x11():
    display = Gdk.Display.get_default()
    return display is not None and type(display).__name__.startswith("X11")


def desktop_bounds(display):
    rects = [display.get_monitor(i).get_geometry() for i in range(display.get_n_monitors())]
    return (min(r.x for r in rects), min(r.y for r in rects),
            max(r.x + r.width for r in rects), max(r.y + r.height for r in rects))


def at_edge(edge, x, y, bounds):
    left, top, right, bottom = bounds
    return {"left": x <= left, "right": x >= right - 1, "top": y <= top, "bottom": y >= bottom - 1}[edge]


def edge_origin(edge, bounds):
    """The edge's reference corner (as in KDE Connect: deltas are measured from it)."""
    left, top, right, bottom = bounds
    return {"left": (left, top), "right": (right, top), "top": (left, top), "bottom": (left, bottom)}[edge]


def return_point(edge, bounds, delta):
    """Where the pointer comes back: just inside the edge, at the same height or width."""
    left, top, right, bottom = bounds
    origin = edge_origin(edge, bounds)
    x, y = origin[0] + delta[0], origin[1] + delta[1]
    inset = 2  # off the edge, so it doesn't go straight back
    x = min(max(x, left + (inset if edge == "left" else 0)), right - 1 - (inset if edge == "right" else 0))
    y = min(max(y, top + (inset if edge == "top" else 0)), bottom - 1 - (inset if edge == "bottom" else 0))
    return round(x), round(y)


class InputCapture:
    """``on_start(device_id, edge, delta)`` and ``on_input(device_id, body)``
    are called on the main loop; ``release(device_id, delta)`` ends it."""

    def __init__(self, on_start, on_input):
        self._on_start, self._on_input = on_start, on_input
        self._edges = {}
        self._timer = 0
        self._armed = True  # False until the pointer has left the edge it came back over
        self._active = None  # (device_id, edge) while captured
        self._window = None
        self._center = (0, 0)
        self.supported = is_x11()

    def set_edges(self, edges):
        self._edges = dict(edges)
        if self._active and self._active[0] not in self._edges.values():
            self.release(self._active[0], None)
        if self._edges and self.supported and not self._timer:
            self._timer = GLib.timeout_add(POLL_MS, self._poll)
        elif not self._edges and self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    # -- watching the edges ----------------------------------------------------------

    def _pointer(self):
        return Gdk.Display.get_default().get_default_seat().get_pointer()

    def _poll(self):
        if not self._edges:
            self._timer = 0
            return False
        if self._active:
            return True
        display = Gdk.Display.get_default()
        _window, x, y, mask = Gdk.get_default_root_window().get_device_position(self._pointer())
        bounds = desktop_bounds(display)
        touching = [edge for edge in self._edges if at_edge(edge, x, y, bounds)]
        if not touching:
            self._armed = True
        elif self._armed and not mask & BUTTON_DOWN:  # not while dragging something
            self._start(self._edges[touching[0]], touching[0], x, y, bounds)
        return True

    # -- captured ----------------------------------------------------------------------

    def _start(self, device_id, edge, x, y, bounds):
        display = Gdk.Display.get_default()
        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_default_size(1, 1)
        window.move(bounds[0], bounds[1])
        window.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK
                          | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.SCROLL_MASK
                          | Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        window.connect("motion-notify-event", self._on_motion)
        window.connect("button-press-event", self._on_button, True)
        window.connect("button-release-event", self._on_button, False)
        window.connect("scroll-event", self._on_scroll)
        window.connect("key-press-event", self._on_key)
        window.show()
        cursor = Gdk.Cursor.new_for_display(display, Gdk.CursorType.BLANK_CURSOR)
        status = display.get_default_seat().grab(
            window.get_window(), Gdk.SeatCapabilities.ALL, False, cursor, None, None, None)
        if status != Gdk.GrabStatus.SUCCESS:
            window.destroy()
            self._armed = False
            print("Could not take the mouse and keyboard: {}".format(status), flush=True)
            return
        self._window = window
        self._active = (device_id, edge)
        self._bounds = bounds
        monitor = display.get_monitor_at_point(x, y).get_geometry()
        self._center = (monitor.x + monitor.width // 2, monitor.y + monitor.height // 2)
        self._scale = display.get_monitor_at_point(x, y).get_scale_factor()
        origin = edge_origin(edge, bounds)
        self._warp(*self._center)
        self._on_start(device_id, edge, ((x - origin[0]) * self._scale, (y - origin[1]) * self._scale))

    def _warp(self, x, y):
        self._pointer().warp(Gdk.Display.get_default().get_default_screen(), x, y)

    def _send(self, body):
        if self._active:
            self._on_input(self._active[0], body)

    def _on_motion(self, _window, event):
        dx, dy = event.x_root - self._center[0], event.y_root - self._center[1]
        if dx or dy:
            self._send({"dx": dx * self._scale, "dy": dy * self._scale})
            self._warp(*self._center)
        return True

    def _on_button(self, _window, event, pressed):
        if event.type != Gdk.EventType.BUTTON_PRESS and pressed:
            return True  # the extra double-click events; the presses already went
        field = {1: "singlehold" if pressed else "singlerelease",
                 2: "middleclick" if pressed else None,
                 3: "rightclick" if pressed else None}.get(event.button)
        if field:
            self._send({field: True})
        return True

    def _on_scroll(self, _window, event):
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            _ok, dx, dy = event.get_scroll_deltas()
        else:
            dx, dy = {Gdk.ScrollDirection.UP: (0, -1), Gdk.ScrollDirection.DOWN: (0, 1),
                      Gdk.ScrollDirection.LEFT: (-1, 0), Gdk.ScrollDirection.RIGHT: (1, 0)}[event.direction]
        if dx or dy:
            # KDE Connect: up and right are positive.
            self._send({"scroll": True, "dx": dx * SCROLL_STEP, "dy": -dy * SCROLL_STEP})
        return True

    def _on_key(self, _window, event):
        state = event.state
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(state & Gdk.ModifierType.MOD1_MASK)
        name = Gdk.keyval_name(event.keyval) or ""
        if ctrl and alt and name == "Escape":  # always gets the mouse and keyboard back
            self.release(self._active[0], None)
            return True
        char = chr(Gdk.keyval_to_unicode(event.keyval) or 0)
        body = key_body(name, char if char != "\0" else "", ctrl=ctrl, alt=alt,
                        shift=bool(state & Gdk.ModifierType.SHIFT_MASK),
                        super_=bool(state & Gdk.ModifierType.SUPER_MASK))
        if body is not None:
            self._send(body)
        return True

    def release(self, device_id, delta):
        """Give the mouse and keyboard back here (``delta`` None: where they left)."""
        if not self._active or self._active[0] != device_id:
            return False
        _device, edge = self._active
        self._active = None
        Gdk.Display.get_default().get_default_seat().ungrab()
        if self._window is not None:
            self._window.destroy()
            self._window = None
        if delta is not None:
            self._warp(*return_point(edge, self._bounds, (delta[0] / self._scale, delta[1] / self._scale)))
        else:
            self._warp(*self._center)
        self._armed = False
        return True
