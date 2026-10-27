"""Generic settings dialog built from a plugin's ``settings_schema``."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


def run_plugin_config(parent, plugin, settings):
    """Show the dialog. Returns the new settings dict, or None if cancelled."""
    dialog = Gtk.Dialog(
        title="Configure {}".format(plugin["name"]), transient_for=parent, modal=True)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Save", Gtk.ResponseType.ACCEPT)
    grid = Gtk.Grid(column_spacing=12, row_spacing=10)
    grid.set_border_width(12)
    controls = {}

    for row, field in enumerate(plugin.get("settings_schema", [])):
        key, kind = field["key"], field.get("type")
        value = settings.get(key, field.get("default"))
        label = Gtk.Label(label=field["label"], xalign=0, wrap=True)
        label.set_max_width_chars(34)
        grid.attach(label, 0, row, 1, 1)
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
        elif kind == "folder":
            control = Gtk.FileChooserButton(
                title=field["label"], action=Gtk.FileChooserAction.SELECT_FOLDER)
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

    dialog.get_content_area().add(grid)
    dialog.show_all()
    accepted = dialog.run() == Gtk.ResponseType.ACCEPT
    values = {}
    if accepted:
        for key, (kind, control) in controls.items():
            if kind == "bool":
                values[key] = control.get_active()
            elif kind == "int":
                values[key] = control.get_value_as_int()
            elif kind == "folder":
                values[key] = control.get_filename() or ""
            elif kind == "multiline":
                buffer = control.get_buffer()
                values[key] = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
            else:
                values[key] = control.get_text()
    dialog.destroy()
    return values if accepted else None
