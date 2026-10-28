"""Pair / accept / deny prompt shown for devices that are not connected."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk
from satellite.i18n import _


class PairingPrompt(Gtk.Box):
    def __init__(self, device, on_pair, on_accept, on_deny):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_vexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name("dialog-question-symbolic", Gtk.IconSize.DIALOG)
        self.pack_start(icon, False, False, 0)

        name = device["name"]
        incoming = bool(device.get("pair_requested"))
        if incoming:
            text = _("{} is asking to pair with this computer.").format(name)
        elif device.get("pair_pending"):
            text = _("Pairing request sent to {}. Waiting for a response.").format(name)
        elif device.get("paired"):
            text = _("This device is paired but it is offline.\nOpen KDE Connect on it to reconnect.")
        elif device.get("secure"):
            text = _("Do you want to pair with {}?").format(name)
        else:
            text = _("Establishing a secure connection with {}…").format(name)
        label = Gtk.Label(label=text, justify=Gtk.Justification.CENTER)
        label.get_style_context().add_class("dim-label")
        self.pack_start(label, False, False, 0)

        if device.get("verification_code"):
            code = Gtk.Label()
            code.set_markup("{}\n<span size='x-large' weight='bold'>{}</span>".format(
                GLib.markup_escape_text(_("Security code")), GLib.markup_escape_text(device["verification_code"])))
            code.set_justify(Gtk.Justification.CENTER)
            code.set_selectable(True)
            self.pack_start(code, False, False, 0)
            hint = Gtk.Label(label=_("Check that both devices show the same code."))
            hint.get_style_context().add_class("dim-label")
            self.pack_start(hint, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        if incoming:
            accept = Gtk.Button(label=_("Accept"))
            accept.get_style_context().add_class("suggested-action")
            accept.connect("clicked", lambda _b: on_accept())
            deny = Gtk.Button(label=_("Deny"))
            deny.connect("clicked", lambda _b: on_deny())
            buttons.pack_start(accept, False, False, 0)
            buttons.pack_start(deny, False, False, 0)
        elif device.get("secure") and not device.get("pair_pending") and not device.get("paired"):
            pair = Gtk.Button(label=_("Pair"))
            pair.get_style_context().add_class("suggested-action")
            pair.connect("clicked", lambda _b: on_pair())
            buttons.pack_start(pair, False, False, 0)
        self.pack_start(buttons, False, False, 0)
