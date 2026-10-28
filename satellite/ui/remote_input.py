"""A touchpad and keyboard for controlling the paired device."""

import time

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gtk, PangoCairo

from satellite.plugins.remoteinput import key_body
from satellite.i18n import _

CLICK_SLOP = 4  # pixels a click may wander before it counts as a drag
CLICK_TIME = 0.35
BUTTONS = {1: "singleclick", 2: "middleclick", 3: "rightclick"}


class RemoteInputWindow(Gtk.Window):
    def __init__(self, parent, device_name, send, speed=1.0, keyboard_ready=False):
        super().__init__(title=_("Control {}").format(device_name), transient_for=parent)
        self.set_default_size(480, 360)
        self._send = send
        self._speed = speed
        self._press = None  # (x, y, time) of the button press being tracked
        self._last = None
        self._moved = 0.0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)
        self.add(box)

        hint = Gtk.Label(xalign=0, wrap=True)
        hint.get_style_context().add_class("dim-label")
        hint.set_text(
            _("Drag in the area below to move the pointer, click to click and scroll to scroll. "
            "Keys you type are sent to {}.").format(device_name)
            + ("" if keyboard_ready else
               _(" To type, switch on the KDE Connect Remote Keyboard on the device.")))
        box.pack_start(hint, False, False, 0)

        frame = Gtk.Frame()
        self.pad = Gtk.DrawingArea()
        self.pad.set_can_focus(True)
        self.pad.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        self.pad.connect("button-press-event", self._on_press)
        self.pad.connect("button-release-event", self._on_release)
        self.pad.connect("motion-notify-event", self._on_motion)
        self.pad.connect("scroll-event", self._on_scroll)
        self.pad.connect("key-press-event", self._on_key)
        self.pad.connect("draw", self._on_draw)
        frame.add(self.pad)
        box.pack_start(frame, True, True, 0)
        self.show_all()
        self.pad.grab_focus()

    def _on_draw(self, widget, cr):
        context = widget.get_style_context()
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        Gtk.render_background(context, cr, 0, 0, width, height)
        layout = widget.create_pango_layout(_("Touchpad"))
        color = context.get_color(Gtk.StateFlags.INSENSITIVE)
        cr.set_source_rgba(color.red, color.green, color.blue, 0.5)
        text_width, text_height = layout.get_pixel_size()
        cr.move_to((width - text_width) / 2, (height - text_height) / 2)
        PangoCairo.show_layout(cr, layout)
        return False

    def _on_press(self, widget, event):
        widget.grab_focus()
        if event.type == Gdk.EventType.BUTTON_PRESS:
            self._press = (event.get_button()[1], time.monotonic())
            self._last = (event.x, event.y)
            self._moved = 0.0
        return True

    def _on_motion(self, _widget, event):
        if self._press is None or self._last is None:
            return False
        dx, dy = event.x - self._last[0], event.y - self._last[1]
        self._last = (event.x, event.y)
        self._moved += abs(dx) + abs(dy)
        if self._moved > CLICK_SLOP:
            self._send({"dx": dx * self._speed, "dy": dy * self._speed})
        return True

    def _on_release(self, _widget, event):
        if self._press is None:
            return False
        button, started = self._press
        self._press = self._last = None
        if self._moved <= CLICK_SLOP and time.monotonic() - started < CLICK_TIME:
            field = BUTTONS.get(button)
            if field:
                self._send({field: True})
        return True

    def _on_scroll(self, _widget, event):
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            _ok, _dx, dy = event.get_scroll_deltas()
        else:
            dy = {Gdk.ScrollDirection.UP: -1, Gdk.ScrollDirection.DOWN: 1}.get(event.direction, 0)
        if dy:
            # KDE Connect scrolls up for positive dy.
            self._send({"scroll": True, "dx": 0, "dy": -dy * 3})
        return True

    def _on_key(self, _widget, event):
        state = event.state
        char = chr(Gdk.keyval_to_unicode(event.keyval) or 0)
        body = key_body(
            Gdk.keyval_name(event.keyval) or "", char if char != "\0" else "",
            ctrl=bool(state & Gdk.ModifierType.CONTROL_MASK),
            alt=bool(state & Gdk.ModifierType.MOD1_MASK),
            shift=bool(state & Gdk.ModifierType.SHIFT_MASK),
            super_=bool(state & Gdk.ModifierType.SUPER_MASK))
        if body is not None:
            self._send(body)
        return True

