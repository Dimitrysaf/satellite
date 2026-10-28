"""A virtual pen tablet through the kernel's /dev/uinput, without libevdev.

The desktop sees a real drawing tablet (pen, eraser, pressure), on X11 and
Wayland alike. Opening /dev/uinput needs permission: Satellite's udev rule
gives it to the user logged in at the screen, as KDE Connect's does.
"""

import fcntl
import os
import struct

UINPUT = "/dev/uinput"

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0
BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH = 0x140, 0x141, 0x14a
ABS_X, ABS_Y, ABS_PRESSURE = 0x00, 0x01, 0x18
INPUT_PROP_POINTER, INPUT_PROP_DIRECT = 0x00, 0x01
BUS_VIRTUAL = 0x06
PRESSURE_MAX = 1023


def _ioc(write, number, size=0):
    return (1 if write else 0) << 30 | size << 16 | ord("U") << 8 | number


SETUP = struct.Struct("HHHH80sI")      # struct uinput_setup
ABS_SETUP = struct.Struct("H2x6i")     # struct uinput_abs_setup
EVENT = struct.Struct("llHHi")         # struct input_event (time left to the kernel)

UI_DEV_CREATE = _ioc(False, 1)
UI_DEV_DESTROY = _ioc(False, 2)
UI_DEV_SETUP = _ioc(True, 3, SETUP.size)
UI_ABS_SETUP = _ioc(True, 4, ABS_SETUP.size)
UI_SET_EVBIT = _ioc(True, 100, 4)
UI_SET_KEYBIT = _ioc(True, 101, 4)
UI_SET_ABSBIT = _ioc(True, 103, 4)
UI_SET_PROPBIT = _ioc(True, 110, 4)


def available():
    """Whether this user may create virtual input devices."""
    return os.access(UINPUT, os.R_OK | os.W_OK)


class VirtualTablet:
    """A pen tablet the size of the device's drawing pad (like KDE Connect's)."""

    def __init__(self, name, width, height, resolution_x, resolution_y,
                 opener=os.open, ioctl=fcntl.ioctl, writer=os.write, closer=os.close):
        self._ioctl, self._writer, self._closer = ioctl, writer, closer
        self._fd = opener(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
        try:
            for prop in (INPUT_PROP_DIRECT, INPUT_PROP_POINTER):
                ioctl(self._fd, UI_SET_PROPBIT, prop)
            for kind in (EV_SYN, EV_KEY, EV_ABS):
                ioctl(self._fd, UI_SET_EVBIT, kind)
            for key in (BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH):
                ioctl(self._fd, UI_SET_KEYBIT, key)
            for code, maximum, resolution in ((ABS_X, width - 1, resolution_x),
                                              (ABS_Y, height - 1, resolution_y),
                                              (ABS_PRESSURE, PRESSURE_MAX, 0)):
                ioctl(self._fd, UI_SET_ABSBIT, code)
                ioctl(self._fd, UI_ABS_SETUP, ABS_SETUP.pack(code, 0, 0, maximum, 0, 0, resolution))
            label = name.encode("utf-8")[:79]
            ioctl(self._fd, UI_DEV_SETUP, SETUP.pack(BUS_VIRTUAL, 0, 0, 1, label, 0))
            ioctl(self._fd, UI_DEV_CREATE)
        except OSError:
            closer(self._fd)
            raise

    def emit(self, events):
        """Write (type, code, value) events, then a sync so they apply together."""
        data = b"".join(EVENT.pack(0, 0, kind, code, int(value)) for kind, code, value in events)
        self._writer(self._fd, data + EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def close(self):
        if self._fd is None:
            return
        try:
            self._ioctl(self._fd, UI_DEV_DESTROY)
        except OSError:
            pass
        self._closer(self._fd)
        self._fd = None
