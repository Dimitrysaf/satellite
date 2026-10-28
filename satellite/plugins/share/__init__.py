"""Send and receive files, text and links."""

import os
import socket
import threading
import time
from urllib.parse import urlparse

from OpenSSL import SSL

from satellite.plugins._base import PacketStatePlugin, PluginAction
from satellite.tls import json_packet, recv_tls, send_tls, tls_handshake
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
PAYLOAD_PORTS = range(1739, 1765)
OFFER_TIMEOUT = 60
CHUNK = 65536


def clean_filename(name):
    name = os.path.basename(str(name or ""))
    name = "".join(char for char in name if char.isprintable() and char not in "/\\")
    return "received-file" if name in ("", ".", "..") else name


def unique_path(directory, filename):
    destination = os.path.join(directory, filename)
    stem, extension = os.path.splitext(filename)
    suffix = 1
    while os.path.exists(destination):
        destination = os.path.join(directory, "{} ({}){}".format(stem, suffix, extension))
        suffix += 1
    return destination


def download_directory(settings):
    configured = os.path.expanduser(str(settings.get("download_dir") or ""))
    if configured and os.path.isabs(configured):
        return configured
    return os.path.join(os.path.expanduser("~"), "Downloads")


def _progress(daemon, link_id, device_name, transfer_id, filename, done, total,
              direction, path=None, finished=False, error=None):
    daemon.on_transfer_progress({
        "id": transfer_id,
        "device_id": link_id,
        "device_name": device_name,
        "filename": filename,
        "received": done,
        "total": total,
        "percent": min(100, int(done * 100 / total)) if total else 100,
        "path": path,
        "finished": finished,
        "error": error,
        "direction": direction,
    })


def receive_file(daemon, link, packet, destination_dir, quiet=False, name=None):
    """Fetch a file payload from the paired device's TLS payload port.

    ``quiet`` skips the transfer popup and notifications (MMS attachments,
    notification icons). ``name`` saves under exactly that name, replacing
    any file already there (icons are cached by content hash).
    """
    size = packet.get("payloadSize")
    transfer = packet.get("payloadTransferInfo", {})
    port = transfer.get("port") if isinstance(transfer, dict) else None
    if (
        isinstance(size, bool) or not isinstance(size, int)
        or size < 0 or size > MAX_FILE_SIZE
        or isinstance(port, bool) or not isinstance(port, int)
        or not 1 <= port <= 65535
    ):
        return None

    filename = clean_filename(name or packet.get("body", {}).get("filename"))
    transfer_id = "{}:{}".format(link["id"], packet.get("id", time.monotonic_ns()))
    report = lambda done, **extra: None if quiet else _progress(  # noqa: E731
        daemon, link["id"], link["name"], transfer_id, filename, done, size,
        "receive", **extra)
    stage = "creating the download directory"
    temporary = connection = raw_socket = None
    received = 0
    last_percent, last_time = -1, 0.0
    try:
        os.makedirs(destination_dir, mode=0o700, exist_ok=True)
        destination = (os.path.join(destination_dir, filename) if name
                       else unique_path(destination_dir, filename))
        report(0)
        stage = "connecting to payload port {}".format(port)
        raw_socket = socket.create_connection((link["address"], port), timeout=12)
        raw_socket.settimeout(12)
        connection = SSL.Connection(daemon.payload_context(server=False), raw_socket)
        connection.set_connect_state()
        stage = "starting the payload TLS handshake"
        tls_handshake(connection, raw_socket)
        peer = connection.get_peer_certificate()
        if not peer or not daemon.verify_peer(link["id"], peer):
            raise SSL.Error("Payload TLS certificate does not match paired device")

        temporary = destination + ".part"
        stage = "receiving {} bytes".format(size)
        with open(temporary, "wb" if name else "xb") as output:
            while received < size:
                chunk = recv_tls(connection, raw_socket, min(CHUNK, size - received))
                if not chunk:
                    raise ConnectionError("File payload ended early")
                output.write(chunk)
                received += len(chunk)
                percent = int(received * 100 / size) if size else 100
                now = time.monotonic()
                if percent != last_percent or now - last_time >= 0.5:
                    last_percent, last_time = percent, now
                    report(received)
        os.replace(temporary, destination)
        temporary = None
        report(size, path=destination, finished=True)
        return destination
    except (OSError, SSL.Error, ConnectionError, ValueError) as error:
        report(received, finished=True, error="{}: {}".format(type(error).__name__, error))
        print("File receive from {} failed while {}: {!r}".format(
            link["name"], stage, error), flush=True)
        return None
    finally:
        for resource in (connection, raw_socket):
            try:
                if resource is not None:
                    resource.close()
            except (OSError, SSL.Error):
                pass
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


LINK_SCHEMES = ("http", "https", "mailto", "tel", "sms", "geo", "maps", "market")


def is_link(text):
    """One URL on its own: sent as a link the phone opens, not as text."""
    text = (text or "").strip()
    if not text or any(c.isspace() for c in text):
        return False
    parts = urlparse(text)
    return parts.scheme.lower() in LINK_SCHEMES and bool(parts.netloc or parts.path)


def send_text(daemon, device_id, text):
    """Send text (copied to the phone's clipboard) or a link (opened on the phone)."""
    text = (text or "").strip()
    if not text:
        return False
    body = {"url": text} if is_link(text) else {"text": text}
    return daemon.send_plugin_packet("share", device_id, "kdeconnect.share.request", body)


def send_file(daemon, device_id, path):
    """Offer a file to a paired device and serve it from a worker thread."""
    link = daemon.link(device_id)
    if link is None or not link.get("paired"):
        return False
    try:
        if not os.path.isfile(path):
            return False
        size = os.path.getsize(path)
        modified = int(os.path.getmtime(path) * 1000)
    except OSError:
        return False
    if size > MAX_FILE_SIZE:
        return False

    server, port = open_payload_server()
    if server is None:
        return False
    filename = clean_filename(path)
    packet = json_packet(
        "kdeconnect.share.request",
        {"filename": filename, "lastModified": modified},
        payload_size=size, payload_port=port,
    )
    if not daemon.send_packet(device_id, packet):
        server.close()
        return False
    transfer_id = "{}:send:{}".format(device_id, time.monotonic_ns())
    threading.Thread(
        target=_serve_file,
        args=(daemon, link, server, path, filename, size, transfer_id),
        daemon=True,
    ).start()
    return True


def open_payload_server():
    """Listen on a free payload port. Returns (server, port) or (None, None)."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    for port in PAYLOAD_PORTS:
        try:
            server.bind(("", port))
        except OSError:
            continue
        server.listen(1)
        server.settimeout(OFFER_TIMEOUT)
        return server, port
    server.close()
    return None, None


def accept_payload_peer(daemon, link, server):
    """Accept the device's payload connection and check its certificate."""
    raw_socket, _address = server.accept()
    server.close()
    raw_socket.settimeout(12)
    connection = SSL.Connection(daemon.payload_context(server=True), raw_socket)
    connection.set_accept_state()
    try:
        tls_handshake(connection, raw_socket)
        peer = connection.get_peer_certificate()
        if not peer or not daemon.verify_peer(link["id"], peer):
            raise SSL.Error("Payload TLS certificate does not match paired device")
    except BaseException:
        raw_socket.close()
        raise
    return connection, raw_socket


def send_bytes(daemon, device_id, packet_type, body, data):
    """Send a packet whose payload is ``data`` (small, in memory)."""
    link = daemon.link(device_id)
    if link is None or not link.get("paired"):
        return False
    server, port = open_payload_server()
    if server is None:
        return False
    packet = json_packet(packet_type, body, payload_size=len(data), payload_port=port)
    if not daemon.send_packet(device_id, packet):
        server.close()
        return False
    threading.Thread(target=_serve_bytes, args=(daemon, link, server, data), daemon=True).start()
    return True


def _serve_bytes(daemon, link, server, data):
    connection = raw_socket = None
    try:
        connection, raw_socket = accept_payload_peer(daemon, link, server)
        for start in range(0, len(data), CHUNK):
            send_tls(connection, raw_socket, data[start:start + CHUNK])
        try:
            connection.shutdown()
        except SSL.Error:
            pass
    except (OSError, SSL.Error, ConnectionError, ValueError) as error:
        print("Payload send to {} failed: {!r}".format(link["name"], error), flush=True)
    finally:
        for resource in (connection, raw_socket, server):
            try:
                if resource is not None:
                    resource.close()
            except (OSError, SSL.Error):
                pass


def _serve_file(daemon, link, server, path, filename, size, transfer_id):
    report = lambda done, **extra: _progress(  # noqa: E731
        daemon, link["id"], link["name"], transfer_id, filename, done, size,
        "send", **extra)
    connection = raw_socket = None
    sent = 0
    try:
        connection, raw_socket = accept_payload_peer(daemon, link, server)
        report(0)
        last_percent, last_time = -1, 0.0
        with open(path, "rb") as source:
            while True:
                chunk = source.read(CHUNK)
                if not chunk:
                    break
                send_tls(connection, raw_socket, chunk)
                sent += len(chunk)
                percent = int(sent * 100 / size) if size else 100
                now = time.monotonic()
                if percent != last_percent or now - last_time >= 0.5:
                    last_percent, last_time = percent, now
                    report(sent)
        try:
            connection.shutdown()
        except SSL.Error:
            pass
        report(size, path=path, finished=True)
    except (OSError, SSL.Error, ConnectionError, ValueError) as error:
        report(sent, finished=True, error="{}: {}".format(type(error).__name__, error))
        print("File send to {} failed: {!r}".format(link["name"], error), flush=True)
    finally:
        for resource in (connection, raw_socket, server):
            try:
                if resource is not None:
                    resource.close()
            except (OSError, SSL.Error):
                pass


class Plugin(PacketStatePlugin):
    plugin_id = "share"
    display_name = _("Share")
    description = _("Send and receive files, text and links.")
    icon = "document-send-symbolic"
    incoming_capabilities = (
        "kdeconnect.share.request",
        "kdeconnect.share.request.update",
    )
    outgoing_capabilities = ("kdeconnect.share.request",)
    packet_types = incoming_capabilities
    settings_schema = (
        {"key": "download_dir", "label": _("Save received files in"), "type": "folder", "default": ""},
        {"key": "copy_text", "label": _("Copy shared text to the clipboard"), "type": "bool", "default": True},
        {"key": "auto_open_links", "label": _("Open shared links automatically"), "type": "bool", "default": False},
    )

    def handle_packet(self, daemon, link, packet):
        if packet.get("type") not in self.packet_types:
            return False
        if packet["type"] == "kdeconnect.share.request.update":
            return True
        body = packet.get("body", {})
        settings = daemon.plugins.get_settings(link["id"], self.plugin_id)
        if packet.get("payloadSize") is not None:
            receive_file(daemon, link, packet, download_directory(settings))
        elif isinstance(body.get("text"), str):
            self._receive_text(daemon, link, settings, body["text"])
        elif isinstance(body.get("url"), str):
            self._receive_url(daemon, link, settings, body["url"])
        return True

    @staticmethod
    def _receive_text(daemon, link, settings, text):
        if settings.get("copy_text", True):
            daemon.set_clipboard(text)
        preview = text if len(text) <= 160 else text[:157] + "…"
        daemon.notify(
            _("Text from {}").format(link["name"]),
            preview + (_("\n(copied to the clipboard)") if settings.get("copy_text", True) else ""),
            icon="edit-copy-symbolic", timeout=8000,
        )

    @staticmethod
    def _receive_url(daemon, link, settings, url):
        if urlparse(url).scheme not in ("http", "https"):
            return
        if settings.get("auto_open_links", False):
            daemon.open_url(url)
        daemon.notify(
            _("Link from {}").format(link["name"]), url, icon="web-browser-symbolic",
            actions=[("open", _("Open"), lambda: daemon.open_url(url))], timeout=10000,
        )

    def actions(self, device):
        def run(ctx):
            paths = ctx.choose_files(_("Send files to {}").format(ctx.device["name"]))
            failed = [os.path.basename(p) for p in paths if not send_file(ctx.daemon, ctx.device_id, p)]
            if failed:
                ctx.error(
                    _("Could not send {}").format(", ".join(failed[:3])),
                    _("The connection is unavailable, or the file could not be read."),
                )

        def send_text_action(ctx):
            clipboard = ctx.clipboard_text() or ""
            text = ctx.ask_text(
                _("Send text or a link to {}").format(ctx.device["name"]),
                _("Text is put on the device's clipboard; a link opens there."),
                clipboard if len(clipboard) <= 2000 else "", _("Send"))
            if text and not send_text(ctx.daemon, ctx.device_id, text):
                ctx.error(_("Could not send"), _("The connection is unavailable."))

        return [
            PluginAction("send-files", _("Send files…"), "document-send-symbolic", run,
                         _("Send files to this device"), opens_window=True),
            PluginAction("send-text", _("Send text or link…"), "insert-link-symbolic", send_text_action,
                         _("Send text, or a link to open on this device"), opens_window=True),
        ]
