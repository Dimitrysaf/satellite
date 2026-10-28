"""Browse the paired device's files, mounted with sshfs like KDE Connect does."""

import os
import shutil
import subprocess
import threading

from satellite.plugins._base import PluginAction
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


def mount_point(device_id):
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(runtime, "satellite", device_id)


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

    def __init__(self, runner=subprocess.run):
        self._runner = runner
        self._lock = threading.Lock()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        return "kdeconnect.sftp.request" in remote_incoming

    def actions(self, device):
        def run(ctx):
            mount = mount_point(ctx.device_id)
            if os.path.ismount(mount):
                ctx.daemon.open_url("file://" + mount)
            elif not shutil.which("sshfs"):
                ctx.error(_("Browsing needs sshfs"), _("Install the sshfs package, then try again."))
            elif not ctx.daemon.send_plugin_packet(
                    self.plugin_id, ctx.device_id, "kdeconnect.sftp.request", {"startBrowsing": True}):
                ctx.error(_("Could not browse the device"), _("The connection is unavailable."))

        return [PluginAction("browse", _("Browse files"), "folder-remote-symbolic", run,
                             _("Open the device's files in the file manager"))]

    def handle_packet(self, daemon, link, packet):
        if packet.get("type") != "kdeconnect.sftp":
            return False
        body = packet.get("body", {})
        if body.get("errorMessage"):
            daemon.notify(_("Could not browse {}").format(link["name"]), str(body["errorMessage"]),
                          icon="dialog-warning-symbolic")
        elif body.get("serverRunning") is False:
            self.on_disconnected(daemon, link["id"])
        elif all(key in body for key in ("user", "port", "password")):
            threading.Thread(target=self._mount, args=(daemon, link, body), daemon=True).start()
        return True

    def _mount(self, daemon, link, body):
        mount = mount_point(link["id"])
        with self._lock:
            if not os.path.ismount(mount):
                os.makedirs(mount, mode=0o700, exist_ok=True)
                identity = getattr(daemon, "_private_key_path", None)
                try:
                    self._runner(
                        sshfs_command(body, link["address"], mount, identity),
                        input=str(body["password"]), text=True, check=True, timeout=30,
                        capture_output=True)
                except (OSError, subprocess.SubprocessError) as error:
                    detail = getattr(error, "stderr", "") or str(error)
                    daemon.notify(_("Could not browse {}").format(link["name"]), detail.strip()[:200],
                                  icon="dialog-warning-symbolic")
                    return
        daemon.open_url("file://" + mount)

    def on_disconnected(self, _daemon, device_id):
        mount = mount_point(device_id)
        if os.path.ismount(mount):
            tool = shutil.which("fusermount3") or shutil.which("fusermount") or "fusermount"
            try:
                self._runner([tool, "-u", mount], check=False, timeout=10, capture_output=True)
            except (OSError, subprocess.SubprocessError):
                pass
