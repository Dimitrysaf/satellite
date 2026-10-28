"""satellite-cli: use Satellite from a terminal, scripts or keyboard shortcuts.

    satellite-cli devices
    satellite-cli ping [-d DEVICE] [MESSAGE]
    satellite-cli ring [-d DEVICE]
    satellite-cli send [-d DEVICE] FILE...
    satellite-cli text [-d DEVICE] TEXT        (a link opens on the phone)
    satellite-cli sms [-d DEVICE] NUMBER TEXT
    satellite-cli sync-contacts [-d DEVICE]

DEVICE is a device name or id. Without it, the only connected device is used.
Satellite is started in the background if it isn't running. ``--pick`` asks
with a small window instead of the terminal (for the file manager menu).

Exit codes: 0 done, 1 failed, 2 no such device or several to choose from,
3 Satellite could not be started.
"""

import argparse
import json
import os
import sys
import time
from satellite.i18n import _, ngettext

OK, FAILED, NO_DEVICE, NO_SATELLITE = 0, 1, 2, 3


class CliError(Exception):
    def __init__(self, message, code, shown=False):
        super().__init__(message)
        self.code = code
        self.shown = shown  # the user already saw it (or cancelled): no second dialog


def find_device(devices, wanted=None, connected_only=True):
    """The device a name or id refers to, or the only connected one."""
    pool = [d for d in devices if d.get("connected")] if connected_only else list(devices)
    if wanted:
        lowered = wanted.lower()
        matches = [d for d in devices if d["id"] == wanted or d["name"].lower() == lowered]
        if not matches:
            raise CliError(_("No device called '{}'.").format(wanted), NO_DEVICE)
        if connected_only and not matches[0].get("connected"):
            raise CliError(_("{} is not connected.").format(matches[0]["name"]), NO_DEVICE)
        return matches[0]
    if len(pool) == 1:
        return pool[0]
    if not pool:
        raise CliError(_("No device is connected."), NO_DEVICE)
    raise CliError(_("Several devices are connected; choose one with -d: {}.").format(
        ", ".join(d["name"] for d in pool)), NO_DEVICE)


def connect(start=True, timeout=10):
    from satellite.phone_client import PhoneClient
    client = PhoneClient()
    if not client.available and start:
        client.start_satellite()
        deadline = time.monotonic() + timeout
        while not client.is_running() and time.monotonic() < deadline:
            time.sleep(0.2)
        time.sleep(0.5)  # give it a moment to find devices
    if not client.is_running():
        raise CliError(_("Satellite is not running and could not be started."), NO_SATELLITE)
    return client


def pick_device(devices, prompt=_("Send to which device?")):
    """A small window to choose a device (used from the file manager)."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    connected = [d for d in devices if d.get("connected")]
    if len(connected) == 1:
        return connected[0]
    if not connected:
        show_error(_("No device is connected"), _("Connect a device with Satellite, then try again."))
        raise CliError(_("No device is connected."), NO_DEVICE, shown=True)
    dialog = Gtk.Dialog(title=_("Send to which device?"), modal=True)
    dialog.set_keep_above(True)
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    dialog.add_button(_("Send"), Gtk.ResponseType.ACCEPT)
    question = Gtk.Label(label=prompt, xalign=0, margin_start=12, margin_top=12)
    dialog.get_content_area().add(question)
    listbox = Gtk.ListBox(margin=12)
    for device in connected:
        row = Gtk.ListBoxRow()
        row.device = device
        row.add(Gtk.Label(label=device["name"], xalign=0, margin=8))
        listbox.add(row)
    listbox.select_row(listbox.get_row_at_index(0))
    listbox.connect("row-activated", lambda *_a: dialog.response(Gtk.ResponseType.ACCEPT))
    dialog.get_content_area().add(listbox)
    dialog.show_all()
    chosen = dialog.run() == Gtk.ResponseType.ACCEPT and listbox.get_selected_row()
    dialog.destroy()
    if not chosen:
        raise CliError(_("Cancelled."), FAILED, shown=True)
    return chosen.device


def show_error(title, text):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.OK, text=title)
    dialog.format_secondary_text(text)
    dialog.set_keep_above(True)
    dialog.run()
    dialog.destroy()


def parse(argv):
    parser = argparse.ArgumentParser(prog="satellite-cli", description=_("Use Satellite from the command line."))
    parser.add_argument("--pick", action="store_true", help=_("choose the device and show errors in a window"))
    commands = parser.add_subparsers(dest="command", required=True)
    devices = commands.add_parser("devices", help=_("list devices"))
    devices.add_argument("--json", action="store_true", help=_("machine-readable output"))

    def with_device(name, help_text):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("-d", "--device", help=_("device name or id"))
        return command

    with_device("ping", _("send a ping")).add_argument("message", nargs="?", default="")
    with_device("ring", _("make the device ring"))
    with_device("send", _("send files")).add_argument("files", nargs="+")
    with_device("text", _("send text, or a link to open on the device")).add_argument("text")
    sms = with_device("sms", _("send a text message (SMS)"))
    sms.add_argument("number")
    sms.add_argument("text")
    with_device("sync-contacts", _("copy the device's contacts again"))
    return parser.parse_args(argv)


def run(args, client, out=sys.stdout):
    devices = client.devices()
    if args.command == "devices":
        if args.json:
            json.dump(devices, out, indent=2)
            out.write("\n")
        else:
            for device in devices:
                state = "connected" if device["connected"] else ("paired" if device["paired"] else _("not paired"))
                out.write("{}  {}  ({})\n".format(device["id"], device["name"], state))
        return OK
    if args.pick and not args.device:
        count = len(getattr(args, "files", ()))
        prompt = ngettext("Send {} file to:", "Send {} files to:", count).format(count) if count else _("Which device?")
        device = pick_device(devices, prompt)
    else:
        device = find_device(devices, args.device)
    target = device["id"]
    if args.command == "ping":
        done = client.ping(target, args.message)
    elif args.command == "ring":
        done = client.ring(target)
    elif args.command == "send":
        paths = [os.path.abspath(path) for path in args.files]
        missing = [p for p in paths if not os.path.isfile(p)]
        if missing:
            raise CliError(_("Not a file: {}").format(", ".join(missing)), FAILED)
        failed = client.send_files(target, paths)
        if failed:
            raise CliError(_("Could not send: {}").format(", ".join(os.path.basename(p) for p in failed)), FAILED)
        done = True
    elif args.command == "text":
        done = client.send_text(target, args.text)
    elif args.command == "sms":
        done = client.send_message(target, [args.number], args.text)
    else:  # sync-contacts
        done = client.sync_contacts(target)
    if not done:
        raise CliError(_("{} could not do that (is the plugin switched on?).").format(device["name"]), FAILED)
    return OK


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    try:
        return run(args, connect())
    except CliError as error:
        if args.pick and not error.shown:  # from the file manager: no terminal to read
            show_error("Satellite", str(error))
        print("satellite-cli: {}".format(error), file=sys.stderr)
        return error.code


if __name__ == "__main__":
    sys.exit(main())
