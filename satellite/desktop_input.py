"""Inject pointer and keyboard input into this desktop, on X11 or Wayland.

Backends, picked once on first use:

* X11: ``xdotool``.
* Wayland: the RemoteDesktop portal (org.freedesktop.portal.RemoteDesktop),
  which asks the user once and then remembers the permission; otherwise
  ``ydotool``, which needs its ``ydotoold`` service running.

Keys are named like xdotool does ("ctrl", "Return", "F5", "a"); buttons are
"left", "middle" or "right". Scroll steps are positive upwards, or to the
right when scrolling sideways.
"""

import os
import shutil
import subprocess
import threading
import time
import uuid
from satellite.i18n import _

# Keysyms for the portal (X11 keysymdef.h values).
KEYSYMS = {
    "BackSpace": 0xff08, "Tab": 0xff09, "Linefeed": 0xff0a, "Return": 0xff0d,
    "Scroll_Lock": 0xff14, "Sys_Req": 0xff15, "Escape": 0xff1b, "Home": 0xff50,
    "Left": 0xff51, "Up": 0xff52, "Right": 0xff53, "Down": 0xff54,
    "Page_Up": 0xff55, "Page_Down": 0xff56, "End": 0xff57, "Delete": 0xffff,
    "Shift_L": 0xffe1, "Control_L": 0xffe3, "Alt_L": 0xffe9, "Super_L": 0xffeb,
    "shift": 0xffe1, "ctrl": 0xffe3, "alt": 0xffe9, "super": 0xffeb,
    **{"F{}".format(n): 0xffbe + n - 1 for n in range(1, 13)},
}

# Linux evdev key codes for ydotool (US layout for letters and digits).
EVDEV_KEYS = {
    "Escape": 1, "BackSpace": 14, "Tab": 15, "Return": 28, "Linefeed": 28,
    "Control_L": 29, "ctrl": 29, "Shift_L": 42, "shift": 42, "Alt_L": 56, "alt": 56,
    "Super_L": 125, "super": 125, "Scroll_Lock": 70, "Sys_Req": 99,
    "Home": 102, "Up": 103, "Page_Up": 104, "Left": 105, "Right": 106,
    "End": 107, "Down": 108, "Page_Down": 109, "Delete": 111, "space": 57,
    **{"F{}".format(n): 58 + n for n in range(1, 11)}, "F11": 87, "F12": 88,
    **dict(zip("1234567890", range(2, 12))),
    **dict(zip("qwertyuiop", range(16, 26))),
    **dict(zip("asdfghjkl", range(30, 39))),
    **dict(zip("zxcvbnm", range(44, 51))),
}

# evdev button codes (portal) and ydotool's button numbers.
EVDEV_BUTTONS = {"left": 0x110, "right": 0x111, "middle": 0x112}
YDOTOOL_BUTTONS = {"left": 0, "right": 1, "middle": 2}
XDOTOOL_BUTTONS = {"left": "1", "middle": "2", "right": "3"}
XDOTOOL_WHEEL = {(False, True): "4", (False, False): "5", (True, False): "6", (True, True): "7"}


def char_keysym(char):
    code = ord(char)
    return code if code < 0x100 else 0x01000000 | code


def screen_size(fallback=(1920, 1080)):
    """The primary monitor's size in device pixels, which input tools work in."""
    try:
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geometry, scale = monitor.get_geometry(), monitor.get_scale_factor()
        return geometry.width * scale, geometry.height * scale
    except (ImportError, ValueError, AttributeError, TypeError):
        return fallback


def is_wayland():
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"


class InputProblem(Exception):
    """Something the user can fix; the message says how."""


class XdotoolBackend:
    name = "xdotool"

    def __init__(self, xdotool, runner=subprocess.run):
        self._xdotool = xdotool
        self._runner = runner

    def _run(self, *arguments):
        self._runner(
            [self._xdotool, *arguments], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )

    def move(self, dx, dy):
        self._run("mousemove_relative", "--sync", "--", str(round(dx)), str(round(dy)))

    def move_to(self, x, y):
        self._run("mousemove", str(round(x)), str(round(y)))

    def scroll(self, steps, horizontal=False):
        button = XDOTOOL_WHEEL[(horizontal, steps > 0)]
        self._run("click", "--repeat", str(abs(steps)), "--delay", "20", button)

    def click(self, button, count=1):
        if count > 1:
            self._run("click", "--repeat", str(count), "--delay", "100", XDOTOOL_BUTTONS[button])
        else:
            self._run("click", XDOTOOL_BUTTONS[button])

    def press(self, button):
        self._run("mousedown", XDOTOOL_BUTTONS[button])

    def release(self, button):
        self._run("mouseup", XDOTOOL_BUTTONS[button])

    def key(self, keys):
        self._run("key", "+".join(keys))

    def type_text(self, text):
        self._run("type", "--clearmodifiers", "--delay", "0", text)


class YdotoolBackend:
    name = "ydotool"
    PASTE_RESTORE_SECONDS = 1.5  # then the clipboard gets its old text back

    def __init__(self, ydotool, runner=subprocess.run, wl_copy=None, wl_paste=None):
        self._ydotool = ydotool
        self._runner = runner
        self._wl_copy = shutil.which("wl-copy") if wl_copy is None else wl_copy
        self._wl_paste = shutil.which("wl-paste") if wl_paste is None else wl_paste

    @staticmethod
    def service_running():
        """Whether ydotoold's socket is there (ydotool does nothing without it)."""
        candidates = [os.environ.get("YDOTOOL_SOCKET"), "/tmp/.ydotool_socket"]
        if os.environ.get("XDG_RUNTIME_DIR"):
            candidates.append(os.path.join(os.environ["XDG_RUNTIME_DIR"], ".ydotool_socket"))
        return any(path and os.path.exists(path) for path in candidates)

    def _run(self, *arguments):
        self._runner(
            [self._ydotool, *arguments], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )

    def move(self, dx, dy):
        self._run("mousemove", "-x", str(round(dx)), "-y", str(round(dy)))

    def move_to(self, x, y):
        self._run("mousemove", "--absolute", "-x", str(round(x)), "-y", str(round(y)))

    def scroll(self, steps, horizontal=False):
        x, y = (steps, 0) if horizontal else (0, steps)
        self._run("mousemove", "--wheel", "-x", str(x), "-y", str(y))

    def click(self, button, count=1):
        code = "0x{:02X}".format(0xC0 | YDOTOOL_BUTTONS[button])
        self._run("click", "--repeat", str(count), "--next-delay", "100", code)

    def press(self, button):
        self._run("click", "0x{:02X}".format(0x40 | YDOTOOL_BUTTONS[button]))

    def release(self, button):
        self._run("click", "0x{:02X}".format(0x80 | YDOTOOL_BUTTONS[button]))

    def key(self, keys):
        codes = []
        for name in keys:
            code = EVDEV_KEYS.get(name, EVDEV_KEYS.get(name.lower()))
            if code is None:
                raise ValueError("No key code for {!r}".format(name))
            codes.append(code)
        events = ["{}:1".format(c) for c in codes] + ["{}:0".format(c) for c in reversed(codes)]
        self._run("key", *events)

    def type_text(self, text):
        # ydotool types with a US keyboard; anything else goes in by pasting.
        if all(char in "\n\t" or " " <= char <= "~" for char in text):
            self._run("type", "--", text)
        elif self._wl_copy:
            self._paste(text)
        else:
            raise InputProblem(_("To type letters outside the US keyboard (such as Greek), "
                               "install wl-clipboard: sudo apt install wl-clipboard"))

    def _clipboard_text(self):
        if not self._wl_paste:
            return None
        try:
            result = self._runner([self._wl_paste, "--no-newline", "--type", "text/plain"],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=1)
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout if getattr(result, "returncode", 1) == 0 else None

    def _set_clipboard(self, data):
        # wl-copy stays in the background to serve the clipboard, so no pipes
        # may be left open to it.
        self._runner([self._wl_copy, "--type", "text/plain;charset=utf-8"], input=data, check=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)

    def _paste(self, text):
        previous = self._clipboard_text()
        self._set_clipboard(text.encode("utf-8"))
        self.key(["ctrl", "v"])
        if previous is not None:
            restore = threading.Timer(self.PASTE_RESTORE_SECONDS, self._restore, (previous,))
            restore.daemon = True
            restore.start()

    def _restore(self, previous):
        try:
            self._set_clipboard(previous)
        except (OSError, subprocess.SubprocessError):
            pass


class PortalBackend:
    """org.freedesktop.portal.RemoteDesktop. The first use shows the
    desktop's permission dialog; the restore token avoids asking again."""

    name = "portal"
    BUS = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"
    IFACE = "org.freedesktop.portal.RemoteDesktop"
    KEYBOARD, POINTER = 1, 2
    ASK_AGAIN_SECONDS = 60  # after a refusal, so every touch doesn't bring the dialog back

    def __init__(self, token_path=None, timeout=120):
        self._token_path = token_path
        self._timeout = timeout
        self._session = None
        self._refused_at = None
        self._lock = threading.Lock()
        self._bus = None

    @classmethod
    def available(cls):
        from gi.repository import Gio, GLib
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = bus.call_sync(
                cls.BUS, cls.PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (cls.IFACE, "AvailableDeviceTypes")),
                GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            return False
        types = reply.unpack()[0]
        return bool(types & cls.KEYBOARD) and bool(types & cls.POINTER)

    def _request(self, method, signature, arguments, options):
        """Call a portal method that answers with a Request::Response signal."""
        from gi.repository import Gio, GLib
        token = "satellite_" + uuid.uuid4().hex
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        request_path = "{}/request/{}/{}".format(self.PATH, sender, token)
        context = GLib.MainContext.new()
        context.push_thread_default()
        loop = GLib.MainLoop.new(context, False)
        result = {}

        def on_response(_bus, _sender, _path, _iface, _signal, params):
            result["code"], result["values"] = params.unpack()
            loop.quit()

        subscription = self._bus.signal_subscribe(
            self.BUS, "org.freedesktop.portal.Request", "Response", request_path,
            None, Gio.DBusSignalFlags.NONE, on_response)
        try:
            options = dict(options, handle_token=GLib.Variant("s", token))
            self._bus.call_sync(
                self.BUS, self.PATH, self.IFACE, method,
                GLib.Variant(signature, arguments + (options,)),
                None, Gio.DBusCallFlags.NONE, -1, None)
            timeout = GLib.timeout_source_new_seconds(self._timeout)
            timeout.set_callback(lambda *_a: loop.quit() or False)
            timeout.attach(context)
            loop.run()
            timeout.destroy()
        finally:
            self._bus.signal_unsubscribe(subscription)
            context.pop_thread_default()
        if result.get("code") != 0:
            raise RuntimeError("{} was refused or cancelled".format(method))
        return result["values"]

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        if self._refused_at is not None and time.monotonic() - self._refused_at < self.ASK_AGAIN_SECONDS:
            raise InputProblem(REFUSED_HELP)
        self._refused_at = None
        try:
            return self._start_session()
        except RuntimeError:
            self._refused_at = time.monotonic()
            raise InputProblem(REFUSED_HELP) from None

    def _start_session(self):
        from gi.repository import Gio, GLib
        self._bus = self._bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        values = self._request(
            "CreateSession", "(a{sv})", (),
            {"session_handle_token": GLib.Variant("s", "satellite_" + uuid.uuid4().hex)})
        session = values["session_handle"]
        select = {
            "types": GLib.Variant("u", self.KEYBOARD | self.POINTER),
            "persist_mode": GLib.Variant("u", 2),  # until revoked
        }
        restore = self._read_token()
        if restore:
            select["restore_token"] = GLib.Variant("s", restore)
        self._request("SelectDevices", "(oa{sv})", (session,), select)
        values = self._request("Start", "(osa{sv})", (session, ""), {})
        if values.get("restore_token"):
            self._write_token(values["restore_token"])
        self._session = session
        return session

    def _read_token(self):
        try:
            with open(self._token_path, "r", encoding="utf-8") as stream:
                return stream.read().strip()
        except (OSError, TypeError):
            return ""

    def _write_token(self, token):
        if not self._token_path:
            return
        try:
            descriptor = os.open(self._token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(token)
        except OSError:
            pass

    def _notify(self, method, signature, *values):
        from gi.repository import Gio, GLib
        with self._lock:
            session = self._ensure_session()
            try:
                self._bus.call_sync(
                    self.BUS, self.PATH, self.IFACE, method,
                    GLib.Variant(signature, (session, {}) + values),
                    None, Gio.DBusCallFlags.NONE, 2000, None)
            except GLib.Error:
                self._session = None  # closed or revoked: start over next time
                raise

    def move(self, dx, dy):
        self._notify("NotifyPointerMotion", "(oa{sv}dd)", float(dx), float(dy))

    def move_to(self, x, y):
        raise NotImplementedError("absolute positions need a screencast stream")

    def scroll(self, steps, horizontal=False):
        # Portal steps grow downwards and to the right; ours grow upwards and to the right.
        self._notify("NotifyPointerAxisDiscrete", "(oa{sv}ui)", 1 if horizontal else 0,
                     steps if horizontal else -steps)

    def _button(self, button, state):
        self._notify("NotifyPointerButton", "(oa{sv}iu)", EVDEV_BUTTONS[button], state)

    def click(self, button, count=1):
        for _ in range(count):
            self._button(button, 1)
            self._button(button, 0)

    def press(self, button):
        self._button(button, 1)

    def release(self, button):
        self._button(button, 0)

    def _keysym(self, keysym, state):
        self._notify("NotifyKeyboardKeysym", "(oa{sv}iu)", keysym, state)

    def key(self, keys):
        syms = []
        for name in keys:
            sym = KEYSYMS.get(name)
            if sym is None and len(name) == 1:
                sym = char_keysym(name)
            if sym is None:
                raise ValueError("No keysym for {!r}".format(name))
            syms.append(sym)
        for sym in syms:
            self._keysym(sym, 1)
        for sym in reversed(syms):
            self._keysym(sym, 0)

    def type_text(self, text):
        for char in text:
            sym = KEYSYMS["Return"] if char == "\n" else char_keysym(char)
            self._keysym(sym, 1)
            self._keysym(sym, 0)


def pick_backend(token_path=None, runner=subprocess.run):
    """The best backend for this session, or None if input can't be injected."""
    if is_wayland():
        if PortalBackend.available():
            return PortalBackend(token_path)
        ydotool = shutil.which("ydotool")
        return YdotoolBackend(ydotool, runner) if ydotool else None
    xdotool = shutil.which("xdotool")
    return XdotoolBackend(xdotool, runner) if xdotool else None


REFUSED_HELP = (_("Remote control of this computer wasn't allowed. Satellite will ask again "
                "in a minute; allow it there to use the phone as a touchpad."))
YDOTOOL_HELP = (_("ydotool isn't working. Start its service (ydotoold) and make sure you may "
                "use /dev/uinput."))


def unavailable_help():
    """What to install when nothing can inject input in this session."""
    if is_wayland():
        return (_("This Wayland desktop offers no remote control permission. Install ydotool "
                "and start its ydotoold service: sudo apt install ydotool"))
    return _("Install xdotool: sudo apt install xdotool")


def problem_text(backend, error=None):
    """What to tell the user when the phone couldn't control this computer."""
    if backend is None:
        return unavailable_help()
    if isinstance(error, InputProblem):
        return str(error)
    if backend.name == "ydotool":
        return YDOTOOL_HELP
    return _("{} failed: {}").format(backend.name, error)


def status_text(backend):
    """One line for the plugin's settings: what moves the pointer here."""
    if backend is None:
        return _("Nothing can control this computer yet. ") + unavailable_help()
    if backend.name == "xdotool":
        return _("Using xdotool (X11).")
    if backend.name == "portal":
        return _("Using the desktop's remote control permission (Wayland).")
    notes = [_("Using ydotool (Wayland).")]
    if not backend.service_running():
        notes.append(_("Its ydotoold service doesn't seem to be running."))
    if not backend._wl_copy:
        notes.append(_("Install wl-clipboard to type letters outside the US keyboard."))
    return " ".join(notes)
