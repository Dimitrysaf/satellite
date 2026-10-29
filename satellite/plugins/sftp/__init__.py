"""Browse the paired device's files, mounted with sshfs like KDE Connect does.

While the device is connected its storage stays mounted in ~/Satellite/<device name>:
GIO shows FUSE mounts in the home folder as drives, so it appears in the file
manager's sidebar (and on the desktop) like an external disk, with an eject button.
"""

import os
import shutil
import subprocess
import threading

from satellite.plugins._base import PluginAction
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


def mounts_root():
    return os.path.join(os.path.expanduser("~"), "Satellite")


def mount_point(device_name, device_id):
    """~/Satellite/<device name>; the folder name is what the file manager shows."""
    name = "".join(char for char in device_name if char not in "/\0").strip().lstrip(".")
    return os.path.join(mounts_root(), name or device_id)


def sshfs_command(body, address, mount, identity_file=None):
    """The sshfs argv for a kdeconnect.sftp body (password goes on stdin)."""
    path = "/" if body.get("multiPaths") else str(body.get("path") or "/")
    host = "[{}]".format(address) if ":" in address else address
    command = [
        "sshfs", "{}@{}:{}".format(body["user"], host, path), mount,
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


class Plugin:
    plugin_id = "sftp"
    display_name = _("Browse files")
    description = _("Open the paired device's files in the file manager.")
    icon = "folder-remote-symbolic"
    incoming_capabilities = ("kdeconnect.sftp",)
    outgoing_capabilities = ("kdeconnect.sftp.request",)
    settings_schema = (
        {"key": "keep_mounted", "label": _("Show the device's files as a drive while it's connected"),
         "type": "bool", "default": True},
    )

    def __init__(self, runner=subprocess.run):
        self._runner = runner
        self._lock = threading.Lock()
        self._mounts = {}  # device id -> mount folder
        self._open_when_mounted = set()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.sftp.request" in remote_incoming

    @staticmethod
    def send(daemon, device_id, packet_type=None, body=None, **_kwargs):
        if packet_type not in Plugin.outgoing_capabilities:
            return False
        return daemon._send_plugin_packet(device_id, packet_type, body or {})

    def on_connected(self, daemon, device_id):
        if shutil.which("sshfs") and daemon.plugins.get_settings(device_id, self.plugin_id).get("keep_mounted", True):
            daemon.send_plugin_packet(self.plugin_id, device_id, "kdeconnect.sftp.request", {"startBrowsing": True})

    def actions(self, device):
        def run(ctx):
            mount = self._mounts.get(ctx.device_id)
            if mount and os.path.ismount(mount):
                ctx.daemon.open_url(file_uri(mount))
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
        with self._lock:
            mount = self._mounts.get(device_id) or mount_point(link["name"], device_id)
            if not os.path.ismount(mount):
                if os.path.isdir(mount) and os.listdir(mount):
                    mount = mount_point("{} ({})".format(link["name"], device_id[:6]), device_id)
                os.makedirs(mount, mode=0o700, exist_ok=True)
                identity = getattr(daemon, "_private_key_path", None)
                try:
                    self._runner(
                        sshfs_command(body, link["address"], mount, identity),
                        input=str(body["password"]), text=True, check=True, timeout=30,
                        capture_output=True)
                except (OSError, subprocess.SubprocessError) as error:
                    _remove_empty(mount)
                    detail = (getattr(error, "stderr", "") or str(error)).strip()[:200]
                    if device_id in self._open_when_mounted:
                        self._open_when_mounted.discard(device_id)
                        daemon.notify(_("Could not browse {}").format(link["name"]), detail,
                                      icon="dialog-warning-symbolic")
                    else:
                        print("Could not mount {}: {}".format(link["name"], detail), flush=True)
                    return
            self._mounts[device_id] = mount
        if device_id in self._open_when_mounted:
            self._open_when_mounted.discard(device_id)
            daemon.open_url(file_uri(mount))

    def on_disconnected(self, _daemon, device_id):
        with self._lock:
            mount = self._mounts.pop(device_id, None)
        self._open_when_mounted.discard(device_id)
        if mount:
            self._unmount(mount)

    def shutdown(self, _daemon):
        """Satellite is quitting: don't leave the devices mounted."""
        with self._lock:
            mounts, self._mounts = list(self._mounts.values()), {}
        for mount in mounts:
            self._unmount(mount)

    def _unmount(self, mount):
        if os.path.ismount(mount):
            tool = shutil.which("fusermount3") or shutil.which("fusermount") or "fusermount"
            try:
                self._runner([tool, "-u", mount], check=False, timeout=10, capture_output=True)
            except (OSError, subprocess.SubprocessError):
                pass
        _remove_empty(mount)


def file_uri(path):
    from urllib.parse import quote
    return "file://" + quote(path)


def _remove_empty(mount):
    """Remove the mount folder, and ~/Satellite once no device uses it."""
    for folder in (mount, mounts_root()):
        try:
            os.rmdir(folder)
        except OSError:
            pass
