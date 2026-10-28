"""Presenter settings with a live preview of the pointer over a slide."""

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from satellite.presenter_look import Trail, draw_pointer, pointer_look
from satellite.i18n import _

PREVIEW_WIDTH, PREVIEW_HEIGHT = 384, 216


class Note:
    """A warning above the settings, with an optional button that fixes it.

    ``action(dialog)`` runs on the button; ``drawn_only`` and ``cursor_only``
    show the note only for the drawn pointers or for the mouse pointer.
    """

    def __init__(self, text, button=None, action=None, drawn_only=False, cursor_only=False):
        self.text, self.button, self.action = text, button, action
        self.drawn_only, self.cursor_only = drawn_only, cursor_only

    def applies(self, style):
        return not ((self.drawn_only and style == "cursor") or (self.cursor_only and style != "cursor"))


def _rgba(text):
    color = Gdk.RGBA()
    color.parse(text or "#000000")
    return color


def _hex(rgba):
    return "#{:02x}{:02x}{:02x}".format(round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255))


class Preview(Gtk.DrawingArea):
    """A slide with the pointer on it. Move the mouse over it to try the pointer."""

    def __init__(self):
        super().__init__()
        self.set_size_request(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self._on_draw)
        self.connect("motion-notify-event", self._on_motion)
        self.settings = {}
        self.x, self.y = 0.62, 0.55
        self.visible = True
        self.trail = Trail()
        self._hide_source = 0
        self._fade_source = 0

    def update(self, settings):
        self.settings = dict(settings)
        self.visible = True
        self._restart_hide()
        self.queue_draw()

    def _on_motion(self, _widget, event):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        self.x, self.y = event.x / width, event.y / height
        self.visible = True
        look = pointer_look(self.settings)
        if look["trail"]:
            self.trail.add(self.x, self.y)
            if not self._fade_source:
                self._fade_source = GLib.timeout_add(30, self._fade)
        self._restart_hide()
        self.queue_draw()

    def _restart_hide(self):
        if self._hide_source:
            GLib.source_remove(self._hide_source)
            self._hide_source = 0
        hide_ms = pointer_look(self.settings)["hide_ms"]
        if hide_ms is not None:
            self._hide_source = GLib.timeout_add(max(hide_ms, 1500), self._hide)

    def _hide(self):
        self._hide_source = 0
        self.visible = False
        self.trail.clear()
        self.queue_draw()
        return False

    def _fade(self):
        self.trail.prune()
        self.queue_draw()
        if self.trail.points:
            return True
        self._fade_source = 0
        return False

    def _on_draw(self, _widget, cr):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        # A plain slide: title and a few bullet lines.
        cr.set_source_rgb(0.96, 0.96, 0.97)
        cr.paint()
        cr.set_source_rgb(0.17, 0.32, 0.55)
        cr.rectangle(0, 0, width, height * 0.2)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.select_font_face("Sans")
        cr.set_font_size(height * 0.09)
        cr.move_to(width * 0.06, height * 0.135)
        cr.show_text(_("Quarterly results"))
        cr.set_source_rgb(0.55, 0.57, 0.6)
        for index, length in enumerate((0.7, 0.55, 0.62, 0.4)):
            cr.rectangle(width * 0.08, height * (0.32 + index * 0.14), width * length, height * 0.05)
            cr.fill()
        look = pointer_look(self.settings)
        if look["style"] == "cursor" or not self.visible:
            return False
        draw_pointer(cr, width, height, self.x, self.y, look, self.trail.faded() if look["trail"] else (),
                     screen_width=_screen_width())
        return False


def _screen_width():
    """Width of the monitor, so the preview shows the pointer at its real size."""
    display = Gdk.Display.get_default()
    if display is None:
        return 1920
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    return monitor.get_geometry().width if monitor else 1920


class PresenterSettings(Gtk.Dialog):
    def __init__(self, parent, settings, schema, notes=()):
        super().__init__(title=_("Presenter"), transient_for=parent, modal=True)
        self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        self.add_button(_("Save"), Gtk.ResponseType.ACCEPT)
        self.settings = dict(settings)
        self._labels = {field["key"]: field["label"] for field in schema}
        self._options = {field["key"]: field.get("options", ()) for field in schema}
        self._rows = {}  # key -> widgets shown or hidden together
        self._controls = {}
        self._shown_style = None

        self._notes = [(note, self._note_bar(note)) for note in notes]
        body = Gtk.Box(spacing=18, border_width=12)
        self.get_content_area().add(body)
        self.grid = Gtk.Grid(column_spacing=12, row_spacing=8, valign=Gtk.Align.START)
        body.pack_start(self.grid, False, False, 0)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.preview = Preview()
        frame = Gtk.Frame()
        frame.add(self.preview)
        side.pack_start(frame, False, False, 0)
        hint = Gtk.Label(label=_("Move the mouse over the slide to try the pointer."), xalign=0, wrap=True,
                         max_width_chars=44)
        hint.get_style_context().add_class("dim-label")
        side.pack_start(hint, False, False, 0)
        self.cursor_note = Gtk.Label(
            label=_("The phone moves your normal mouse pointer. To change how it looks, open "
                  "Themes → Mouse pointer in the system settings."), xalign=0, wrap=True, max_width_chars=44)
        side.pack_start(self.cursor_note, False, False, 0)
        body.pack_start(side, True, True, 0)

        self._row = 0
        self._heading(_("Pointer"))
        self._choice("style")
        self._picture()
        self._color("color")
        self._choice("size")
        self._percent("opacity")
        self._choice("outline")
        self._color("outline_color")
        self._switch("trail")
        self._heading(_("Spotlight"), key="spotlight_heading")
        self._percent("spotlight_darkness")
        self._choice("spotlight_size")
        self._switch("spotlight_ring")
        self._heading(_("Behaviour"))
        self._choice("hide_after")
        self._choice("speed")

        self.show_all()
        self._refresh()

    def _note_bar(self, note):
        bar = Gtk.InfoBar(message_type=Gtk.MessageType.WARNING)
        label = Gtk.Label(label=note.text, xalign=0, wrap=True, max_width_chars=70)
        bar.get_content_area().add(label)
        if note.button:
            bar.add_button(note.button, Gtk.ResponseType.APPLY)

            def fixed(bar, _response):
                note.action(self)
                self._notes.remove((note, bar))
                bar.destroy()
                self.resize(1, 1)
            bar.connect("response", fixed)
        self.get_content_area().pack_start(bar, False, False, 0)
        return bar

    def choose(self, key, value):
        """Pick a value in one of the choice rows, as if the user had."""
        self._controls[key].set_active_id(str(value))

    # -- rows ------------------------------------------------------------------

    def _attach(self, key, label, control):
        caption = Gtk.Label(label=label, xalign=0)
        self.grid.attach(caption, 0, self._row, 1, 1)
        self.grid.attach(control, 1, self._row, 1, 1)
        self._rows[key] = (caption, control)
        self._row += 1

    def _heading(self, text, key=None):
        label = Gtk.Label(xalign=0, margin_top=6 if self._row else 0)
        label.set_markup("<b>{}</b>".format(GLib.markup_escape_text(text)))
        self.grid.attach(label, 0, self._row, 2, 1)
        if key:
            self._rows[key] = (label,)
        self._row += 1

    def _set(self, key, value):
        self.settings[key] = value
        self._refresh()

    def _choice(self, key):
        combo = Gtk.ComboBoxText()
        for value, text in self._options[key]:
            combo.append(str(value), text)
        combo.set_active_id(str(self.settings.get(key)))
        combo.connect("changed", lambda c: self._set(key, c.get_active_id()))
        self._controls[key] = combo
        self._attach(key, self._labels[key], combo)

    def _color(self, key):
        button = Gtk.ColorButton.new_with_rgba(_rgba(self.settings.get(key)))
        button.set_halign(Gtk.Align.START)
        button.connect("color-set", lambda b: self._set(key, _hex(b.get_rgba())))
        self._attach(key, self._labels[key], button)

    def _percent(self, key):
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 10, 100 if key == "opacity" else 95, 5)
        scale.set_value(int(self.settings.get(key) or 0))
        scale.set_size_request(160, -1)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.connect("value-changed", lambda s: self._set(key, int(s.get_value())))
        self._attach(key, self._labels[key].replace(" (%)", ""), scale)

    def _switch(self, key):
        switch = Gtk.Switch(active=bool(self.settings.get(key)), halign=Gtk.Align.START)
        switch.connect("notify::active", lambda s, _p: self._set(key, s.get_active()))
        self._attach(key, self._labels[key], switch)

    def _picture(self):
        chooser = Gtk.FileChooserButton(title=_("Choose a picture"), action=Gtk.FileChooserAction.OPEN)
        images = Gtk.FileFilter()
        images.set_name("Pictures")
        images.add_pixbuf_formats()
        chooser.add_filter(images)
        if self.settings.get("image"):
            chooser.set_filename(self.settings["image"])
        chooser.connect("file-set", lambda c: self._set("image", c.get_filename() or ""))
        self._attach("image", self._labels["image"], chooser)

    # -- showing what applies --------------------------------------------------

    def _refresh(self):
        style = self.settings.get("style", "dot")
        drawn = style in ("dot", "image")
        visible = {
            # Colour: the dot, the spotlight's ring, or a picture's trail.
            "color": style in ("dot", "spotlight") or (style == "image" and bool(self.settings.get("trail"))),
            "size": drawn, "opacity": style != "cursor", "trail": drawn,
            "outline": style == "dot", "outline_color": style == "dot" and self.settings.get("outline") != "none",
            "image": style == "image",
            "spotlight_heading": style == "spotlight", "spotlight_darkness": style == "spotlight",
            "spotlight_size": style == "spotlight", "spotlight_ring": style == "spotlight",
            "hide_after": style != "cursor",
        }
        for key, widgets in self._rows.items():
            for widget in widgets:
                widget.set_visible(visible.get(key, True))
        self.cursor_note.set_visible(style == "cursor")
        for note, bar in self._notes:
            bar.set_visible(note.applies(style))
        self.preview.update(self.settings)
        if style != self._shown_style:
            self._shown_style = style
            self.resize(1, 1)  # shrink to fit the rows that are left


def run_presenter_settings(parent, settings, schema, notes=()):
    """Returns the new settings, or None if cancelled."""
    dialog = PresenterSettings(parent, settings, schema, notes)
    accepted = dialog.run() == Gtk.ResponseType.ACCEPT
    values = dialog.settings
    dialog.destroy()
    return values if accepted else None
