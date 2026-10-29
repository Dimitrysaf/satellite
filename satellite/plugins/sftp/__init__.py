"""Browse the paired device's files, mounted with sshfs like KDE Connect does.

While the device is connected its storage stays mounted, starting at the phone's own
storage (/storage/emulated/0), never the folders above it. By default the mount is
in a hidden folder (the session's runtime folder, as KDE Connect does) and gets a
bookmark in the file manager's sidebar while it lasts. Mounted in a visible folder in
the home folder instead, GIO shows it as a drive, with an eject button.
"""

import os
import shutil
import subprocess
import threading
from urllib.parse import quote

from satellite.plugins._base import PluginAction
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


def default_root():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(runtime, "satellite")


def mount_point(root, device_name, device_id):
    """<root>/<device name>; the folder name is what the file manager shows."""
    name = "".join(char for char in device_name if char not in "/\0").strip().lstrip(".")
    return os.path.join(root, name or device_id)


def shows_as_drive(path):
    """GIO lists FUSE mounts as drives only in the home folder, and not inside hidden folders."""
    relative = os.path.relpath(path, os.path.expanduser("~"))
    return not relative.startswith("..") and not any(part.startswith(".") for part in relative.split(os.sep))


def storage_path(body):
    """The phone's own storage, the first of the folders it shares (Android: /storage/emulated/0).

    With several storage folders the phone sets "path" to "/", the top of its whole
    file system; the device's storage starts at the first of "multiPaths".
    """
    paths = [str(path) for path in body.get("multiPaths") or () if path]
    return paths[0] if paths else str(body.get("path") or "/")


def sshfs_command(body, address, mount, identity_file=None):
    """The sshfs argv for a kdeconnect.sftp body (password goes on stdin)."""
    host = "[{}]".format(address) if ":" in address else address
    command = [
        "sshfs", "{}@{}:{}".format(body["user"], host, storage_path(body)), mount,
        "-p", str(body["port"]),
        "-s",  # avoids out-of-order chunks corrupting files (as KDE Connect)
        "-F", "/dev/null",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "uid={}".format(os.getuid()), "-o", "gid={}".format(os.getgid()),
        "-o", "reconnect", "-o", "ServerAliveInterval=30",
        "-o", "password_stdin",
    ]
    if identity_file and os.path.exists(identity_file):
        command += ["-o", "IdentityFile={}".format(identity_file)]
    return command


def file_uri(path):
    return "file://" + quote(path)


def bookmarks_path():
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(config, "gtk-3.0", "bookmarks")


def set_bookmark(path, label, present):
    """Add or remove the file manager's sidebar bookmark for ``path``, leaving the others alone."""
    uri = file_uri(path)
    file = bookmarks_path()
    try:
        with open(file, encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    except FileNotFoundError:
        lines = []
    except OSError:
        return
    kept = [line for line in lines if line.split(" ", 1)[0] != uri]
    if present:
        kept.append("{} {}".format(uri, label))
    if kept == lines:
        return
    try:
        os.makedirs(os.path.dirname(file), exist_ok=True)
        with open(file + ".tmp", "w", encoding="utf-8") as stream:
            stream.write("".join(line + "\n" for line in kept))
        os.replace(file + ".tmp", file)
    except OSError as error:
        print("Could not update the file manager bookmarks: {}".format(error), flush=True)


class Plugin:
    plugin_id = "sftp"
    display_name = _("Browse files")
    description = _("Open the paired device's files in the file manager.")
    icon = "folder-remote-symbolic"
    incoming_capabilities = ("kdeconnect.sftp",)
    outgoing_capabilities = ("kdeconnect.sftp.request",)
    settings_schema = (
        {"key": "keep_mounted", "label": _("Keep the device's files mounted while it's connected"),
         "type": "bool", "default": True},
        {"key": "folder", "label": _("Mount in this folder (empty: a hidden folder)"),
         "type": "folder", "default": ""},
    )

    def __init__(self, runner=subprocess.run):
        self._runner = runner
        self._lock = threading.Lock()
        self._mounts = {}  # device id -> (mount folder, folder it was made in, bookmarked)
        self._open_when_mounted = set()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.sftp.request" in remote_incoming

    @staticmethod
    def send(daemon, device_id, packet_type=None, body=None, **_kwargs):
        if packet_type not in Plugin.outgoing_capabilities:
            return False
        return daemon._send_plugin_packet(device_id, packet_type, body or {})

    @staticmethod
    def settings_notes(_daemon, _device_id):
        return [_("In a hidden folder, the device appears in the file manager's sidebar while it's "
                  "mounted. In a visible folder in your home folder, it appears as a drive.")]

    def on_connected(self, daemon, device_id):
        if shutil.which("sshfs") and daemon.plugins.get_settings(device_id, self.plugin_id).get("keep_mounted", True):
            daemon.send_plugin_packet(self.plugin_id, device_id, "kdeconnect.sftp.request", {"startBrowsing": True})

    def actions(self, device):
        def run(ctx):
            mounted = self._mounts.get(ctx.device_id)
            if mounted and os.path.ismount(mounted[0]):
                ctx.daemon.open_url(file_uri(mounted[0]))
            elif not shutil.which("sshfs"):
                ctx.error(_("Browsing needs sshfs"), _("Install the sshfs package, then try again."))
            else:
                self._open_when_mounted.add(ctx.device_id)
                if not ctx.daemon.send_plugin_packet(
                        self.plugin_id, ctx.device_id, "kdeconnect.sftp.request", {"startBrowsing": True}):
                    self._open_when_mounted.discard(ctx.device_id)
                    ctx.error(_("Could not browse the device"), _("The connection is unavailable."))

        return [PluginAction("browse", _("Browse files"), "folder-remote-symbolic", run,
                             _("Open the device's files in the file manager"))]

    def handle_packet(self, daemon, link, packet):
        if packet.get("type") != "kdeconnect.sftp":
            return False
        body = packet.get("body", {})
        if body.get("errorMessage"):
            # Only worth a notification when someone asked to browse, not on every connection.
            if link["id"] in self._open_when_mounted:
                self._open_when_mounted.discard(link["id"])
                daemon.notify(_("Could not browse {}").format(link["name"]), str(body["errorMessage"]),
                              icon="dialog-warning-symbolic")
            else:
                print("Could not mount {}: {}".format(link["name"], body["errorMessage"]), flush=True)
        elif body.get("serverRunning") is False:
            self.on_disconnected(daemon, link["id"])
        elif all(key in body for key in ("user", "port", "password")):
            threading.Thread(target=self._mount, args=(daemon, link, body), daemon=True).start()
        return True

    def _mount(self, daemon, link, body):
        device_id = link["id"]
        root = daemon.plugins.get_settings(device_id, self.plugin_id).get("folder") or default_root()
        with self._lock:
            mounted = self._mounts.get(device_id)
            mount = mounted[0] if mounted else mount_point(root, link["name"], device_id)
            if not os.path.ismount(mount):
                if os.path.isdir(mount) and os.listdir(mount):
                    mount = mount_point(root, "{} ({})".format(link["name"], device_id[:6]), device_id)
                os.makedirs(mount, mode=0o700, exist_ok=True)
                identity = getattr(daemon, "_private_key_path", None)
                try:
                    self._runner(
                        sshfs_command(body, link["address"], mount, identity),
                        input=str(body["password"]), text=True, check=True, timeout=30,
                        capture_output=True)
                except (OSError, subprocess.SubprocessError) as error:
                    _remove_empty(mount, root)
                    detail = (getattr(error, "stderr", "") or str(error)).strip()[:200]
                    if device_id in self._open_when_mounted:
                        self._open_when_mounted.discard(device_id)
                        daemon.notify(_("Could not browse {}").format(link["name"]), detail,
                                      icon="dialog-warning-symbolic")
                    else:
                        print("Could not mount {}: {}".format(link["name"], detail), flush=True)
                    return
            bookmarked = not shows_as_drive(mount)
            if bookmarked:
                set_bookmark(mount, link["name"], True)
            self._mounts[device_id] = (mount, root, bookmarked)
        if device_id in self._open_when_mounted:
            self._open_when_mounted.discard(device_id)
            daemon.open_url(file_uri(mount))

    def on_disconnected(self, _daemon, device_id):
        with self._lock:
            mounted = self._mounts.pop(device_id, None)
        self._open_when_mounted.discard(device_id)
        if mounted:
            self._unmount(*mounted)

    def shutdown(self, _daemon):
        """Satellite is quitting: don't leave the devices mounted."""
        with self._lock:
            mounts, self._mounts = list(self._mounts.values()), {}
        for mounted in mounts:
            self._unmount(*mounted)

    def _unmount(self, mount, root, bookmarked):
        if bookmarked:
            set_bookmark(mount, "", False)
        if os.path.ismount(mount):
            tool = shutil.which("fusermount3") or shutil.which("fusermount") or "fusermount"
            try:
                self._runner([tool, "-u", mount], check=False, timeout=10, capture_output=True)
            except (OSError, subprocess.SubprocessError):
                pass
        _remove_empty(mount, root)


def _remove_empty(mount, root):
    """Remove the mount folder; and the folder holding it, if it's Satellite's own."""
    folders = [mount] + ([root] if root == default_root() else [])
    for folder in folders:
        try:
            os.rmdir(folder)
        except OSError:
            pass
