"""The Messages plugin's settings: Google Messages for web and signing out of it.

Shown by Satellite (the plugin's ⚙ button), so it may run while Satellite
Messages is closed or open. The Messages window follows messages.json, and
signing out goes through it when it's open, since its web view holds the data.
"""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from satellite_sms import APP_ID, google_messages
from satellite_sms.settings import MessagesSettings
from satellite.i18n import _

GOOGLE_WARNING = _("""Messages will be shown by Google's website instead of by Satellite.

• Satellite can't see, check or control what Google shows or does in this view.
• Messages go through Google's servers and the internet, not only your local network. Your phone needs internet access, and Google Messages must be its messaging app.
• Google's terms and privacy policy apply. Google can change or stop this service, or stop it from working here.
• The pairing stays on this computer until you sign out, so anyone using this computer account can read your messages.
• Only messages.google.com opens here; links in messages open in your web browser. Camera, microphone, location and website notifications are blocked.
• To unpair later, sign out in the Messages plugin settings, or on the phone open Google Messages → Device pairing.""")

SIGN_OUT_ACTION = "google-sign-out"


def ask(parent, title, text, accept, check=None):
    """A confirmation dialog. With ``check``, returns "checked"/"unchecked"/None."""
    dialog = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.WARNING,
                               buttons=Gtk.ButtonsType.NONE, text=title)
    dialog.format_secondary_text(text)
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    dialog.add_button(accept, Gtk.ResponseType.ACCEPT)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    box = None
    if check:
        box = Gtk.CheckButton(label=check, active=True)
        box.set_margin_start(12)
        dialog.get_message_area().pack_start(box, False, False, 0)
        box.show()
    accepted = dialog.run() == Gtk.ResponseType.ACCEPT
    checked = box.get_active() if box else False
    dialog.destroy()
    if not accepted:
        return None if check else False
    return ("checked" if checked else "unchecked") if check else True


def message(parent, title, text):
    dialog = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.INFO,
                               buttons=Gtk.ButtonsType.OK, text=title)
    dialog.format_secondary_text(text)
    dialog.run()
    dialog.destroy()


def sign_out():
    """Forget the Google pairing: through Satellite Messages if it's open, else directly."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        owner = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", (APP_ID,)), GLib.VariantType("(b)"),
                              Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
    except GLib.Error:
        owner = False
    if owner:
        path = "/" + APP_ID.replace(".", "/")
        Gio.DBusActionGroup.get(bus, APP_ID, path).activate_action(SIGN_OUT_ACTION, None)
    else:
        google_messages.delete_data()


def run(parent):
    settings = MessagesSettings()
    dialog = Gtk.Dialog(title=_("Messages settings"), transient_for=parent, modal=True)
    dialog.add_button(_("Close"), Gtk.ResponseType.CLOSE)
    grid = Gtk.Grid(column_spacing=16, row_spacing=14, border_width=16)
    dialog.get_content_area().add(grid)

    labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
    labels.pack_start(Gtk.Label(label=_("Use Google Messages for web"), xalign=0), False, False, 0)
    hint = Gtk.Label(label=_("Satellite Messages shows Google's website instead of the phone's SMS and MMS. "
                             "It can show RCS chats."), xalign=0, wrap=True, max_width_chars=40)
    hint.get_style_context().add_class("dim-label")
    labels.pack_start(hint, False, False, 0)
    grid.attach(labels, 0, 0, 1, 1)
    switch = Gtk.Switch(active=settings.get("google_messages"), valign=Gtk.Align.CENTER)
    grid.attach(switch, 1, 0, 1, 1)

    sign_out_button = Gtk.Button(label=_("Sign out of Google Messages…"), halign=Gtk.Align.START)
    grid.attach(sign_out_button, 0, 1, 2, 1)

    def on_state_set(_switch, turning_on):
        if turning_on == settings.get("google_messages"):
            return False
        if turning_on:
            if not google_messages.available():
                message(dialog, _("Google Messages for web needs WebKitGTK"),
                        _("Install the gir1.2-webkit2-4.1 package, then try again."))
                return True
            if not ask(dialog, _("Use Google Messages for web?"), GOOGLE_WARNING, _("Use Google Messages")):
                return True
            settings.set("google_messages", True)
        else:
            choice = ask(dialog, _("Stop using Google Messages for web?"),
                         _("Satellite Messages will show your phone's SMS and MMS again."), _("Stop Using"),
                         check=_("Also sign out and delete Google Messages data from this computer"))
            if not choice:
                return True
            settings.set("google_messages", False)
            if choice == "checked":
                sign_out()
        switch.set_state(turning_on)
        return True

    def on_sign_out(_button):
        if ask(dialog, _("Sign out of Google Messages?"),
               _("This computer forgets the pairing. To use it again, scan a new QR code with the phone."),
               _("Sign Out")):
            sign_out()

    switch.connect("state-set", on_state_set)
    sign_out_button.connect("clicked", on_sign_out)
    dialog.show_all()
    dialog.run()
    dialog.destroy()
