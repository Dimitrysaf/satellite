"""Finding devices beyond the local broadcast.

* mDNS (Avahi): like KDE Connect, Satellite announces ``_kdeconnect._udp``
  and connects to phones that announce it. This works on networks that
  block broadcasts (some routers, guest Wi-Fi).
* Addresses added by hand: Satellite sends its identity straight to them,
  for phones on another subnet or over a VPN.

Both are switches in Preferences, next to the usual broadcast.
"""

import json
import os
import threading

from gi.repository import Gio, GLib

SERVICE_TYPE = "_kdeconnect._udp"
AVAHI = "org.freedesktop.Avahi"
IF_UNSPEC, PROTO_INET = -1, 0

DEFAULTS = {
    "broadcast": True,       # announce on the local network (the classic way)
    "mdns": True,            # announce and find devices with mDNS (Avahi)
    "custom_devices": [],    # addresses (IP or host name) to contact directly
}


class DiscoverySettings:
    def __init__(self, config_dir):
        self.path = os.path.join(config_dir, "discovery.json")
        self._lock = threading.Lock()
        self._data = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
        try:
            with open(self.path, "r", encoding="utf-8") as stream:
                loaded = json.load(stream)
            for key, default in DEFAULTS.items():
                if isinstance(loaded.get(key), type(default)):
                    self._data[key] = loaded[key]
        except (OSError, ValueError, AttributeError):
            pass
        self._data["custom_devices"] = clean_addresses(self._data["custom_devices"])

    def get(self, key):
        with self._lock:
            value = self._data[key]
            return list(value) if isinstance(value, list) else value

    def set(self, key, value):
        if key not in DEFAULTS or not isinstance(value, type(DEFAULTS[key])):
            raise ValueError("Unknown or invalid setting: {}".format(key))
        if key == "custom_devices":
            value = clean_addresses(value)
        with self._lock:
            self._data[key] = value
            temporary = self.path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(self._data, stream, indent=2, sort_keys=True)
            os.replace(temporary, self.path)


def clean_addresses(addresses):
    """Keep plausible host names / IP addresses, without duplicates."""
    cleaned = []
    for address in addresses:
        if not isinstance(address, str):
            continue
        address = address.strip()
        if address and len(address) <= 253 and all(c.isalnum() or c in ".-:_" for c in address) \
                and address not in cleaned:
            cleaned.append(address)
    return cleaned


def txt_records(records):
    """Avahi TXT records (lists of bytes) as a dict."""
    result = {}
    for record in records or ():
        text = bytes(record).decode("utf-8", "replace")
        if "=" in text:
            key, value = text.split("=", 1)
            result[key] = value
    return result


class MdnsDiscovery:
    """Announce this computer and report KDE Connect devices found by Avahi.

    Runs its own GLib main loop in a thread, so it works in the headless
    daemon as well as inside the app. ``on_found(id, name, type, protocol,
    address, port)`` is called from that thread.
    """

    def __init__(self, identity, port, on_found, bus_address=None):
        self._identity = identity      # {"id", "name", "type", "protocol"}
        self._port = port
        self._on_found = on_found
        self._bus_address = bus_address  # tests use a private bus
        self._loop = None
        self._thread = None
        self.running = False

    def start(self):
        if self._thread is not None:
            return
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self._thread.start()
        ready.wait(5)

    def stop(self):
        if self._loop is not None:
            self._loop.quit()
        self._thread = None
        self.running = False

    def _run(self, ready):
        context = GLib.MainContext.new()
        context.push_thread_default()
        self._loop = GLib.MainLoop.new(context, False)
        try:
            self._bus = self._connect()
            self._announce()
            self._browse()
            self.running = True
        except GLib.Error as error:
            print("mDNS discovery unavailable (is avahi-daemon running?): {}".format(error.message), flush=True)
            self.running = False
            ready.set()
            return
        ready.set()
        self._loop.run()
        context.pop_thread_default()

    def _connect(self):
        if self._bus_address:
            return Gio.DBusConnection.new_for_address_sync(
                self._bus_address,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None)
        return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def _server(self, method, args, reply):
        return self._bus.call_sync(
            AVAHI, "/", "org.freedesktop.Avahi.Server", method, args,
            GLib.VariantType.new(reply) if reply else None, Gio.DBusCallFlags.NONE, 5000, None).unpack()

    def _announce(self):
        path = self._server("EntryGroupNew", None, "(o)")[0]
        identity = self._identity
        txt = [("{}={}".format(k, identity[v])).encode("utf-8")
               for k, v in (("id", "id"), ("name", "name"), ("type", "type"), ("protocol", "protocol"))]
        self._bus.call_sync(
            AVAHI, path, "org.freedesktop.Avahi.EntryGroup", "AddService",
            GLib.Variant("(iiussssqaay)", (IF_UNSPEC, PROTO_INET, 0, identity["id"], SERVICE_TYPE,
                                           "", "", self._port, txt)),
            None, Gio.DBusCallFlags.NONE, 5000, None)
        self._bus.call_sync(AVAHI, path, "org.freedesktop.Avahi.EntryGroup", "Commit", None, None,
                            Gio.DBusCallFlags.NONE, 5000, None)

    def _browse(self):
        # Listen before creating the browser, so no early result is missed.
        self._bus.signal_subscribe(AVAHI, "org.freedesktop.Avahi.ServiceBrowser", "ItemNew", None, None,
                                   Gio.DBusSignalFlags.NONE, self._on_item)
        self._server("ServiceBrowserNew", GLib.Variant(
            "(iissu)", (IF_UNSPEC, PROTO_INET, SERVICE_TYPE, "", 0)), "(o)")

    def _on_item(self, _bus, _sender, _path, _iface, _signal, params):
        interface, protocol, name, service_type, domain, _flags = params.unpack()
        if service_type != SERVICE_TYPE or name == self._identity["id"]:
            return
        try:
            resolved = self._server("ResolveService", GLib.Variant(
                "(iisssiu)", (interface, protocol, name, service_type, domain, PROTO_INET, 0)),
                "(iissssisqaayu)")
        except GLib.Error:
            return
        address, port, txt = resolved[7], resolved[8], txt_records(resolved[9])
        try:
            protocol_version = int(txt.get("protocol", "0"))
        except ValueError:
            protocol_version = 0
        self._on_found(txt.get("id") or name, txt.get("name") or name, txt.get("type") or "phone",
                       protocol_version, address, port)
