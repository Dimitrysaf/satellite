"""Talk to Satellite from Satellite Messages and Satellite Contacts.

If Satellite isn't running it is started in the background: by D-Bus
activation when installed, otherwise from the source checkout.
"""

import json

from gi.repository import Gio, GLib, GObject

from satellite.phone_service import BUS_NAME, INTERFACE, PATH


class PhoneClient(GObject.Object):
    __gsignals__ = {
        "available": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "devices-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "messages-received": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        "attachment-received": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
        "contacts-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, bus=None):
        super().__init__()
        self._bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.available = self._name_has_owner()  # known now, not after the first signal
        self._bus.signal_subscribe(
            BUS_NAME, INTERFACE, None, PATH, None, Gio.DBusSignalFlags.NONE, self._on_signal)
        self._watch = Gio.bus_watch_name_on_connection(
            self._bus, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            lambda *_a: self._set_available(True), lambda *_a: self._set_available(False))

    def _name_has_owner(self):
        try:
            return self._bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", GLib.Variant("(s)", (BUS_NAME,)), GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        except GLib.Error:
            return False

    def is_running(self):
        """Ask the bus now (for scripts that don't run a main loop)."""
        return self._name_has_owner()

    def _set_available(self, available):
        if available != self.available:
            self.available = available
            self.emit("available", available)

    def start_satellite(self):
        """Start Satellite in the background if nobody owns its name."""
        try:
            self._bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "StartServiceByName", GLib.Variant("(su)", (BUS_NAME, 0)), None,
                Gio.DBusCallFlags.NONE, 10000, None)
            return True
        except GLib.Error:
            from satellite.launch import launch
            return launch("satellite", "satellite.satellite", "--background")

    def _call(self, method, signature=None, values=(), reply="(s)", default=None):
        try:
            result = self._bus.call_sync(
                BUS_NAME, PATH, INTERFACE, method,
                GLib.Variant(signature, values) if signature else None,
                GLib.VariantType.new(reply), Gio.DBusCallFlags.NO_AUTO_START, 5000, None)
        except GLib.Error as error:
            print("Satellite {} failed: {}".format(method, error.message), flush=True)
            return default
        return result.unpack()[0]

    def _json(self, method, signature=None, values=()):
        text = self._call(method, signature, values, default="[]")
        try:
            return json.loads(text)
        except ValueError:
            return []

    def devices(self):
        return self._json("GetDevices")

    def conversations(self, device):
        return self._json("GetConversations", "(s)", (device,))

    def thread(self, device, thread):
        return self._json("GetThread", "(sx)", (device, thread))

    def request_conversations(self, device):
        return self._call("RequestConversations", "(s)", (device,), "(b)", False)

    def request_thread(self, device, thread, start=0, count=0):
        return self._call("RequestThread", "(sxxi)", (device, thread, start, count), "(b)", False)

    def send_message(self, device, addresses, text, files=()):
        return self._call("SendMessage", "(sassas)", (device, list(addresses), text, list(files)),
                          "(b)", False)

    def request_attachment(self, device, part, unique_identifier):
        return self._call("RequestAttachment", "(sxs)", (device, part, unique_identifier), "(b)", False)

    def sync_contacts(self, device):
        return self._call("SyncContacts", "(s)", (device,), "(b)", False)

    def ping(self, device, message=""):
        return self._call("Ping", "(ss)", (device, message), "(b)", False)

    def ring(self, device):
        return self._call("Ring", "(s)", (device,), "(b)", False)

    def send_files(self, device, paths):
        """Returns the paths that could not be sent."""
        return self._call("SendFiles", "(sas)", (device, list(paths)), "(as)", list(paths))

    def send_text(self, device, text):
        return self._call("SendText", "(ss)", (device, text), "(b)", False)

    def contacts_folder(self):
        from satellite.plugins.contacts import contacts_root
        return self._call("ContactsFolder", default=None) or contacts_root()

    def _on_signal(self, _bus, _sender, _path, _iface, name, params):
        values = params.unpack() if params is not None else ()
        if name == "DevicesChanged":
            self.emit("devices-changed")
        elif name == "MessagesReceived":
            try:
                messages = json.loads(values[1])
            except ValueError:
                messages = []
            self.emit("messages-received", values[0], messages)
        elif name == "AttachmentReceived":
            self.emit("attachment-received", *values)
        elif name == "ContactsChanged":
            self.emit("contacts-changed", values[0])
