"""Generic settings dialog built from a plugin's ``settings_schema``."""

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, Gtk
from satellite.i18n import _


def _app_icon(info):
    """An image for an app seen showing notifications: its launcher's icon if known."""
    desktop = info.get("desktop")
    if desktop:
        try:
            app = Gio.DesktopAppInfo.new(desktop if desktop.endswith(".desktop") else desktop + ".desktop")
        except TypeError:
            app = None
        if app is not None and app.get_icon() is not None:
            return Gtk.Image.new_from_gicon(app.get_icon(), Gtk.IconSize.LARGE_TOOLBAR)
    icon = info.get("icon") or ""
    if icon.startswith("file://"):
        icon = icon[len("file://"):]
    if icon.startswith("/"):
        return Gtk.Image.new_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(icon)), Gtk.IconSize.LARGE_TOOLBAR)
    if icon and Gtk.IconTheme.get_default().has_icon(icon):
        return Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
    return Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.LARGE_TOOLBAR)


def _app_switches(field, muted):
    """One switch per app (on: its notifications are sent). Returns (widget, switches)."""
    switches = {}
    if not field.get("options"):
        label = Gtk.Label(label=_("Apps show up here once they show a notification while a device "
                                "is connected."), xalign=0, wrap=True, max_width_chars=40)
        label.get_style_context().add_class("dim-label")
        return label, switches
    rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    for name, info in field["options"]:
        box = Gtk.Box(spacing=10, margin=6)
        box.pack_start(_app_icon(info), False, False, 0)
        box.pack_start(Gtk.Label(label=name, xalign=0), True, True, 0)
        switch = Gtk.Switch(active=name not in muted, valign=Gtk.Align.CENTER)
        box.pack_end(switch, False, False, 0)
        rows.add(box)
        switches[name] = switch
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, shadow_type=Gtk.ShadowType.IN)
    scroller.set_propagate_natural_height(True)  # as tall as the apps, up to a point
    scroller.set_max_content_height(260)
    scroller.add(rows)
    return scroller, switches


def run_plugin_config(parent, plugin, settings, notes=(), buttons=()):
    """Show the dialog. Returns the new settings dict, or None if cancelled.

    ``notes`` are lines about the plugin's state, shown above the settings;
    ``buttons`` are (label, callback(dialog)) actions shown below them.
    """
    dialog = Gtk.Dialog(
        title=_("Configure {}").format(plugin["name"]), transient_for=parent, modal=True)
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    dialog.add_button(_("Save"), Gtk.ResponseType.ACCEPT)
    grid = Gtk.Grid(column_spacing=12, row_spacing=10)
    grid.set_border_width(12)
    controls = {}
    first = 0
    for note in notes:
        label = Gtk.Label(label=note, xalign=0, wrap=True, max_width_chars=48)
        label.get_style_context().add_class("dim-label")
        grid.attach(label, 0, first, 2, 1)
        first += 1

    row = first
    for field in plugin.get("settings_schema", []):
        key, kind = field["key"], field.get("type")
        value = settings.get(key, field.get("default"))
        label = Gtk.Label(label=field["label"], xalign=0, wrap=True)
        label.set_max_width_chars(34)
        grid.attach(label, 0, row, 1, 1)
        if kind == "apps":
            # A list under its label, across both columns.
            label.set_margin_top(6)
            widget, control = _app_switches(field, set(value or ()))
            grid.attach(widget, 0, row + 1, 2, 1)
            controls[key] = (kind, control)
            row += 2
            continue
        if kind == "bool":
            control = Gtk.Switch()
            control.set_active(bool(value))
            control.set_halign(Gtk.Align.END)
            control.set_valign(Gtk.Align.CENTER)
            widget = control
        elif kind == "int":
            control = Gtk.SpinButton.new_with_range(
                field.get("minimum", 0), field.get("maximum", 100), 1)
            control.set_value(value or 0)
            widget = control
        elif kind == "choice":
            # ``options`` is a list of (value, label); ids are the str() of the values.
            control = Gtk.ComboBoxText()
            for option_value, option_label in field["options"]:
                control.append(str(option_value), option_label)
            control.set_active_id(str(value))
            widget = control
        elif kind == "color":
            rgba = Gdk.RGBA()
            rgba.parse(str(value or "#000000"))
            control = Gtk.ColorButton.new_with_rgba(rgba)
            control.set_halign(Gtk.Align.END)
            widget = control
        elif kind in ("folder", "file"):
            action = (Gtk.FileChooserAction.SELECT_FOLDER if kind == "folder"
                      else Gtk.FileChooserAction.OPEN)
            control = Gtk.FileChooserButton(title=field["label"], action=action)
            if value:
                control.set_filename(str(value))
            widget = control
        elif kind == "multiline":
            control = Gtk.TextView()
            control.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            control.get_buffer().set_text(str(value or ""))
            widget = Gtk.ScrolledWindow()
            widget.set_size_request(380, 140)
            widget.add(control)
        else:
            control = Gtk.Entry()
            control.set_text(str(value if value is not None else ""))
            control.set_width_chars(28)
            widget = control
        grid.attach(widget, 1, row, 1, 1)
        controls[key] = (kind, control)
        row += 1

    for label, callback in buttons:
        button = Gtk.Button(label=label, halign=Gtk.Align.START)
        button.connect("clicked", lambda _b, cb=callback: cb(dialog))
        grid.attach(button, 0, row, 2, 1)
        row += 1

    dialog.get_content_area().add(grid)
    dialog.show_all()
    accepted = dialog.run() == Gtk.ResponseType.ACCEPT
    values = {}
    if accepted:
        for key, (kind, control) in controls.items():
            if kind == "apps":
                values[key] = sorted(name for name, switch in control.items() if not switch.get_active())
            elif kind == "bool":
                values[key] = control.get_active()
            elif kind == "int":
                values[key] = control.get_value_as_int()
            elif kind == "choice":
                field = next(f for f in plugin["settings_schema"] if f["key"] == key)
                active = control.get_active_id()
                values[key] = next(
                    (v for v, _label in field["options"] if str(v) == active),
                    field.get("default"))
            elif kind == "color":
                rgba = control.get_rgba()
                values[key] = "#{:02x}{:02x}{:02x}".format(
                    round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255))
            elif kind in ("folder", "file"):
                values[key] = control.get_filename() or ""
            elif kind == "multiline":
                buffer = control.get_buffer()
                values[key] = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
            else:
                values[key] = control.get_text()
    dialog.destroy()
    return values if accepted else None
