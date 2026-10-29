"""org.x.Satellite.Phone: what Satellite Messages and Satellite Contacts use.

Exported by the Satellite application on its own bus name, so a companion
app can D-Bus-activate Satellite (in the background) when it isn't running.
Structured data travels as JSON strings to keep the interface small.

ClipboardReceived says that a device's clipboard was put on this computer's
(for clipboard histories, to show where a copy came from). It never carries
the text, only its SHA-256 to recognise it: any application on the bus sees
signals.
"""

import hashlib
import json

from gi.repository import Gio, GLib

BUS_NAME = "org.x.Satellite"
PATH = "/org/x/Satellite/Phone"
INTERFACE = "org.x.Satellite.Phone"

XML = """
<node>
  <interface name="org.x.Satellite.Phone">
    <method name="GetDevices"><arg type="s" direction="out"/></method>
    <method name="GetConversations">
      <arg type="s" name="device" direction="in"/><arg type="s" direction="out"/>
    </method>
    <method name="GetThread">
      <arg type="s" name="device" direction="in"/><arg type="x" name="thread" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="RequestConversations">
      <arg type="s" name="device" direction="in"/><arg type="b" direction="out"/>
    </method>
    <method name="RequestThread">
      <arg type="s" name="device" direction="in"/><arg type="x" name="thread" direction="in"/>
      <arg type="x" name="start" direction="in"/><arg type="i" name="count" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="SendMessage">
      <arg type="s" name="device" direction="in"/><arg type="as" name="addresses" direction="in"/>
      <arg type="s" name="text" direction="in"/><arg type="as" name="files" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="RequestAttachment">
      <arg type="s" name="device" direction="in"/><arg type="x" name="part" direction="in"/>
      <arg type="s" name="unique_identifier" direction="in"/><arg type="b" direction="out"/>
    </method>
    <method name="SyncContacts">
      <arg type="s" name="device" direction="in"/><arg type="b" direction="out"/>
    </method>
    <method name="ContactsFolder"><arg type="s" direction="out"/></method>
    <method name="Ping">
      <arg type="s" name="device" direction="in"/><arg type="s" name="message" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="Ring">
      <arg type="s" name="device" direction="in"/><arg type="b" direction="out"/>
    </method>
    <method name="SendFiles">
      <arg type="s" name="device" direction="in"/><arg type="as" name="paths" direction="in"/>
      <arg type="as" name="failed" direction="out"/>
    </method>
    <method name="SendText">
      <arg type="s" name="device" direction="in"/><arg type="s" name="text" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="SendClipboard">
      <arg type="s" name="device" direction="in"/><arg type="s" name="text" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <signal name="DevicesChanged"/>
    <signal name="MessagesReceived"><arg type="s" name="device"/><arg type="s" name="messages"/></signal>
    <signal name="AttachmentReceived">
      <arg type="s" name="device"/><arg type="s" name="unique_identifier"/><arg type="s" name="path"/>
    </signal>
    <signal name="ContactsChanged"><arg type="s" name="device"/></signal>
    <signal name="ClipboardReceived">
      <arg type="s" name="device"/><arg type="s" name="name"/><arg type="s" name="sha256"/>
    </signal>
  </interface>
</node>
"""


def device_summary(daemon, device):
    """What the companion apps need to know about one device."""
    enabled = {p["id"] for p in device.get("available_plugins", []) if p.get("enabled")}
    return {
        "id": device["id"], "name": device["name"], "type": device.get("type", "phone"),
        "connected": bool(device.get("connected")), "paired": bool(device.get("paired")),
        "sms": "sms" in enabled, "contacts": "contacts" in enabled, "clipboard": "clipboard" in enabled,
    }


class PhoneService:
    def __init__(self, get_window):
        self._get_window = get_window  # returns the main window, creating it if needed
        self._connection = None
        self._registration = 0
        self._last_devices = None

    # -- registration --------------------------------------------------------

    def register(self, connection):
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        self._connection = connection
        self._registration = connection.register_object(
            PATH, node.interfaces[0], self._on_call, None, None)

    def unregister(self):
        if self._connection and self._registration:
            self._connection.unregister_object(self._registration)
        self._registration = 0

    def attach(self, window):
        """Start forwarding daemon events once the window (and daemon) exist."""
        window.daemon.event_listeners.append(
            lambda name, device_id, payload: GLib.idle_add(self._forward, name, device_id, payload))
        window.device_listeners.append(self._devices_changed)
        window.clipboard.on_received = self._clipboard_received

    # -- signals ---------------------------------------------------------------

    def _emit(self, name, signature=None, values=()):
        if self._connection is None:
            return
        self._connection.emit_signal(
            None, PATH, INTERFACE, name, GLib.Variant(signature, values) if signature else None)

    def _forward(self, name, device_id, payload):
        if name == "sms.messages":
            self._emit("MessagesReceived", "(ss)", (device_id, json.dumps(payload)))
        elif name == "sms.attachment":
            self._emit("AttachmentReceived", "(sss)",
                       (device_id, payload["unique_identifier"], payload["path"]))
        elif name == "contacts.changed":
            self._emit("ContactsChanged", "(s)", (device_id,))
        return False

    def _clipboard_received(self, device, text):
        self._emit("ClipboardReceived", "(sss)", (device["id"], device["name"],
                                                   hashlib.sha256(text.encode()).hexdigest()))

    def _devices_changed(self, devices):
        window = self._get_window()
        summary = [device_summary(window.daemon, d) for d in devices]
        if summary != self._last_devices:
            self._last_devices = summary
            self._emit("DevicesChanged")

    # -- methods -----------------------------------------------------------------

    def _on_call(self, _connection, _sender, _path, _iface, method, params, invocation):
        window = self._get_window()
        daemon = window.daemon
        sms = daemon.plugins.plugins.get("sms")
        contacts = daemon.plugins.plugins.get("contacts")
        args = params.unpack()
        try:
            if method == "GetDevices":
                result = ("s", json.dumps([device_summary(daemon, d) for d in window.current_devices()]))
            elif method == "GetConversations":
                result = ("s", json.dumps(sms.conversations(args[0]) if sms else []))
            elif method == "GetThread":
                result = ("s", json.dumps(sms.thread(args[0], args[1]) if sms else []))
            elif method == "RequestConversations":
                result = ("b", bool(sms and sms.request_conversations(daemon, args[0])))
            elif method == "RequestThread":
                result = ("b", bool(sms and sms.request_conversation(daemon, *args)))
            elif method == "SendMessage":
                result = ("b", bool(sms and sms.send_message(daemon, args[0], args[1], args[2], args[3])))
            elif method == "RequestAttachment":
                result = ("b", bool(sms and sms.request_attachment(daemon, *args)))
            elif method == "SyncContacts":
                result = ("b", bool(contacts and contacts.sync(daemon, args[0])))
            elif method == "Ping":
                result = ("b", bool(daemon.send_ping(args[0], args[1] or None)))
            elif method == "Ring":
                result = ("b", bool(daemon.ring_device(args[0])))
            elif method == "SendFiles":
                from satellite.plugins.share import send_file
                result = ("as", [path for path in args[1] if not send_file(daemon, args[0], path)])
            elif method == "SendText":
                from satellite.plugins.share import send_text
                result = ("b", bool(send_text(daemon, args[0], args[1])))
            elif method == "SendClipboard":
                result = ("b", bool(window.clipboard.send_to(args[0], args[1])))
            elif method == "ContactsFolder":
                from satellite.plugins.contacts import contacts_root
                result = ("s", contacts_root())
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
                return
        except Exception as error:  # report, never crash the app
            invocation.return_dbus_error("org.x.Satellite.Error.Failed", str(error))
            return
        invocation.return_value(GLib.Variant("({})".format(result[0]), (result[1],)))
