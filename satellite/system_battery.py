"""Show a connected phone's battery to the system, like a Bluetooth controller's.

Linux batteries live in the kernel (/sys/class/power_supply) and UPower, which the
power applet and System Monitor read, only lists those. The kernel's HID driver
creates one for any HID device that reports a battery, so Satellite creates a
virtual HID device through /dev/uhid that reports the phone's charge. A udev rule
(60-satellite-battery.rules) lets the logged-in user open /dev/uhid and tells
UPower the battery belongs to a phone.
"""

import os
import select
import struct
import threading

UHID_PATH = "/dev/uhid"
UHID_DESTROY, UHID_GET_REPORT, UHID_GET_REPORT_REPLY, UHID_CREATE2, UHID_INPUT2 = 1, 9, 10, 11, 12
EVENT_SIZE = 4 + 4372  # sizeof(struct uhid_event): the type, then its largest member (create2)
BUS_VIRTUAL = 0x06
REPORT_ID = 1

# One report: the battery strength (0-100), whether it's charging, and one key. The kernel
# only keeps a HID battery for a device that also has an input, so a never-pressed
# "phone" key gives it one.
REPORT_DESCRIPTOR = bytes([
    0x05, 0x0C,        # Usage Page (Consumer)
    0x09, 0x01,        # Usage (Consumer Control)
    0xA1, 0x01,        # Collection (Application)
    0x85, REPORT_ID,   #   Report ID
    0x05, 0x06,        #   Usage Page (Generic Device Controls)
    0x09, 0x20,        #   Usage (Battery Strength)
    0x15, 0x00,        #   Logical Minimum (0)
    0x26, 0x64, 0x00,  #   Logical Maximum (100)
    0x75, 0x08,        #   Report Size (8)
    0x95, 0x01,        #   Report Count (1)
    0x81, 0x02,        #   Input (Data, Variable, Absolute)
    0x05, 0x85,        #   Usage Page (Battery System)
    0x09, 0x44,        #   Usage (Charging)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x01,        #   Logical Maximum (1)
    0x75, 0x01,        #   Report Size (1)
    0x95, 0x01,        #   Report Count (1)
    0x81, 0x02,        #   Input (Data, Variable, Absolute)
    0x05, 0x0C,        #   Usage Page (Consumer)
    0x09, 0x8C,        #   Usage (Phone)
    0x81, 0x02,        #   Input (Data, Variable, Absolute)
    0x75, 0x06,        #   Report Size (6)
    0x81, 0x03,        #   Input (Constant): padding to a byte
    0xC0,              # End Collection
])


def uniq_for(device_id):
    """The kernel names the battery hid-<uniq>-battery; the udev rule matches hid-satellite-*."""
    return "satellite-" + "".join(char for char in device_id if char.isalnum())[:48]


def create_event(name, uniq):
    payload = struct.pack(
        "<128s64s64sHHIIII4096s",
        name.encode("utf-8")[:127], b"satellite", uniq.encode("ascii"),
        len(REPORT_DESCRIPTOR), BUS_VIRTUAL, 0, 0, 0, 0, REPORT_DESCRIPTOR)
    return _event(UHID_CREATE2, payload)


def report(charge, charging):
    return bytes([REPORT_ID, max(0, min(100, int(charge))), 1 if charging else 0])


def input_event(data):
    return _event(UHID_INPUT2, struct.pack("<H4096s", len(data), data))


def get_report_reply(request_id, data):
    return _event(UHID_GET_REPORT_REPLY, struct.pack("<IHH4096s", request_id, 0, len(data), data))


def _event(kind, payload):
    return struct.pack("<I", kind) + payload.ljust(EVENT_SIZE - 4, b"\0")


def available():
    return os.access(UHID_PATH, os.R_OK | os.W_OK)


class VirtualBattery:
    """One phone's battery. ``update`` reports a new charge; ``close`` removes it."""

    def __init__(self, device_id, name):
        self._fd = os.open(UHID_PATH, os.O_RDWR | os.O_CLOEXEC)
        self._report = report(0, False)
        self._lock = threading.Lock()
        self._closed = threading.Event()
        os.write(self._fd, create_event(name, uniq_for(device_id)))
        self._thread = threading.Thread(target=self._serve, name="satellite-battery", daemon=True)
        self._thread.start()

    def update(self, charge, charging):
        with self._lock:
            self._report = report(charge, charging)
            if not self._closed.is_set():
                os.write(self._fd, input_event(self._report))

    def close(self):
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
            try:
                os.write(self._fd, _event(UHID_DESTROY, b""))
            finally:
                os.close(self._fd)

    def _serve(self):
        """Answer the kernel when it asks for the current charge (it waits for a reply)."""
        while not self._closed.is_set():
            try:
                readable, _, _ = select.select([self._fd], [], [], 0.5)
                if not readable:
                    continue
                event = os.read(self._fd, EVENT_SIZE)
            except (OSError, ValueError):
                return
            if len(event) >= 9 and struct.unpack_from("<I", event)[0] == UHID_GET_REPORT:
                request_id = struct.unpack_from("<I", event, 4)[0]
                with self._lock:
                    if not self._closed.is_set():
                        os.write(self._fd, get_report_reply(request_id, self._report))


class SystemBatteries:
    """The virtual batteries of the connected phones."""

    def __init__(self):
        self._batteries = {}
        self._lock = threading.Lock()
        self._warned = False

    def update(self, device_id, name, charge, charging):
        if charge < 0:  # the phone doesn't know its charge
            self.remove(device_id)
            return
        with self._lock:
            battery = self._batteries.get(device_id)
            if battery is None:
                if not available():
                    if not self._warned:
                        self._warned = True
                        print("Can't show phone batteries to the system: no access to {} "
                              "(the udev rule takes effect after a reboot or re-login).".format(UHID_PATH),
                              flush=True)
                    return
                try:
                    battery = self._batteries[device_id] = VirtualBattery(device_id, name)
                except OSError as error:
                    print("Could not create the system battery for {}: {}".format(name, error), flush=True)
                    return
        try:
            battery.update(charge, charging)
        except OSError as error:
            print("Could not update the system battery for {}: {}".format(name, error), flush=True)
            self.remove(device_id)

    def remove(self, device_id):
        with self._lock:
            battery = self._batteries.pop(device_id, None)
        if battery is not None:
            battery.close()

    def remove_all(self):
        with self._lock:
            batteries, self._batteries = list(self._batteries.values()), {}
        for battery in batteries:
            battery.close()
