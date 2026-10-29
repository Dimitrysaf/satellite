"""The page for a device that is not connected, worded like KDE Connect for Android."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk
from satellite.i18n import _


class PairingPrompt(Gtk.Box):
    def __init__(self, device, on_pair, on_accept, on_deny, on_cancel):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_vexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_margin_start(24)
        self.set_margin_end(24)

        if device.get("pair_requested"):
            title = _("Pairing request from '{}'").format(device["name"])
            text = None
            buttons = ((_("Accept"), on_accept, True), (_("Reject"), on_deny, False))
        elif device.get("pair_pending"):
            title = _("Pair requested")
            text = None
            buttons = ((_("Cancel pairing"), on_cancel, False),)
        elif device.get("paired"):
            title = _("Device not reachable")
            text = _("This paired device is not reachable. Make sure it is connected to your same network.")
            buttons = ()
        elif device.get("secure"):
            title = _("Device not paired")
            text = _("Pairing two devices will give them access to each other. Only pair together your own devices.")
            buttons = ((_("Request pairing"), on_pair, True),)
        else:
            title = _("Device not reachable")
            text = None
            buttons = ()

        heading = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True, max_width_chars=48)
        heading.set_markup("<span size='large' weight='bold'>{}</span>".format(GLib.markup_escape_text(title)))
        self.pack_start(heading, False, False, 0)
        if text:
            label = Gtk.Label(label=text, justify=Gtk.Justification.CENTER, wrap=True, max_width_chars=48)
            label.get_style_context().add_class("dim-label")
            self.pack_start(label, False, False, 0)

        if device.get("verification_code") and (device.get("pair_requested") or device.get("pair_pending")):
            code = Gtk.Label(selectable=True)
            code.set_markup("<span size='x-large' weight='bold'>🔑 {}</span>".format(
                GLib.markup_escape_text(device["verification_code"])))
            self.pack_start(code, False, False, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER)
        for label, callback, suggested in buttons:
            button = Gtk.Button(label=label)
            if suggested:
                button.get_style_context().add_class("suggested-action")
            button.connect("clicked", lambda _b, cb=callback: cb())
            box.pack_start(button, False, False, 0)
        self.pack_start(box, False, False, 0)
