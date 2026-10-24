#!/usr/bin/python3
"""KDE Connect v8 LAN discovery, TLS links, and pairing."""

import json
import hashlib
import copy
import fcntl
import os
import platform
import re
import select
import shutil
import socket
import struct
import subprocess
import threading
import time
import uuid

from OpenSSL import SSL, crypto
from satellite.plugins import PluginManager

PROJECT_VERSION = "__PROJECT_VERSION__"

UDP_PORT = 1716
TCP_PORTS = range(1716, 1765)
DISCOVERY_INTERVAL = 15
DEVICE_TIMEOUT = 90
MAX_PACKET_SIZE = 8192
MAX_CONCURRENT_LINKS = 42
MAX_TRACKED_DEVICES = 1024
PAIR_TIMEOUT = 90
PAIR_TIMESTAMP_WINDOW = 30 * 60
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,38}$")


def _json_packet(packet_type, body):
    return (json.dumps({
        "id": int(time.time() * 1000),
        "type": packet_type,
        "body": body,
    }, separators=(",", ":")) + "\n").encode("utf-8")


def _read_line(stream, limit=MAX_PACKET_SIZE):
    data = bytearray()
    while len(data) < limit:
        chunk = stream.recv(1)
        if not chunk:
            raise ConnectionError("Peer closed the connection")
        if chunk == b"\n":
            return json.loads(data.decode("utf-8"))
        data.extend(chunk)
    raise ValueError("KDE Connect packet exceeded the size limit")


def _send_all(stream, data):
    sent = 0
    while sent < len(data):
        count = stream.send(data[sent:])
        if count <= 0:
            raise ConnectionError("Peer closed the connection while sending")
        sent += count


def _read_tls_line(stream, raw_socket, limit=MAX_PACKET_SIZE, timeout=8):
    data = bytearray()
    deadline = time.monotonic() + timeout
    while len(data) < limit:
        if stream.pending() <= 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for a TLS packet")
            readable, _, _ = select.select([raw_socket], [], [], remaining)
            if not readable:
                raise TimeoutError("Timed out waiting for a TLS packet")
        try:
            chunk = stream.recv(1)
        except SSL.WantReadError:
            continue
        if not chunk:
            raise ConnectionError("Peer closed the TLS connection")
        if chunk == b"\n":
            return json.loads(data.decode("utf-8"))
        data.extend(chunk)
    raise ValueError("KDE Connect packet exceeded the size limit")


def _tls_handshake(stream, raw_socket, timeout=12):
    deadline = time.monotonic() + timeout
    while True:
        try:
            stream.do_handshake()
            return
        except SSL.WantReadError:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([raw_socket], [], [], remaining)[0]:
                raise TimeoutError("Timed out waiting for the payload TLS handshake")
        except SSL.WantWriteError:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [raw_socket], [], remaining)[1]:
                raise TimeoutError("Timed out sending the payload TLS handshake")


def _recv_tls(stream, raw_socket, size, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Timed out receiving a payload TLS record")
        try:
            if stream.pending() <= 0 and not select.select(
                [raw_socket], [], [], remaining
            )[0]:
                raise TimeoutError("Timed out receiving a payload TLS record")
            return stream.recv(size)
        except SSL.WantReadError:
            continue
        except SSL.WantWriteError:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [raw_socket], [], remaining)[1]:
                raise TimeoutError("Timed out sending a payload TLS response")


class SatelliteDaemon:
    def __init__(self, on_devices_changed=None, on_transfer_progress=None):
        self.on_devices_changed = on_devices_changed or (lambda _devices: None)
        self.on_transfer_progress = on_transfer_progress or (lambda _progress: None)
        self.config_dir = os.path.join(os.path.expanduser("~"), ".config", "satellite")
        os.makedirs(self.config_dir, mode=0o700, exist_ok=True)
        os.chmod(self.config_dir, 0o700)
        self.plugins = PluginManager(self.config_dir)
        self.device_id = self._load_device_id()
        self.device_name = self._clean_device_name(platform.node() or "Satellite")
        self._certificate_path = os.path.join(self.config_dir, "device.crt")
        self._private_key_path = os.path.join(self.config_dir, "device.key")
        self._certificate, self._private_key = self._load_or_create_certificate()
        self._trusted_dir = os.path.join(self.config_dir, "trusted-devices")
        os.makedirs(self._trusted_dir, mode=0o700, exist_ok=True)
        os.chmod(self._trusted_dir, 0o700)

        self._devices = {}
        self._active = {}
        self._connecting = set()
        self._last_connect_attempt = {}
        self._reconnect_timers = {}
        self._reconnect_attempts = {}
        self._pair_timers = {}
        self._connection_slots = threading.BoundedSemaphore(MAX_CONCURRENT_LINKS)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._refresh_requested = threading.Event()
        self._thread = None
        self._udp = None
        self._tcp = None
        self.tcp_port = None
        self._xdotool = shutil.which("xdotool")

    @staticmethod
    def _clean_device_name(value):
        invalid = "\"',;:.!?()[]<>"
        value = "".join(char for char in value if char not in invalid and ord(char) >= 32)
        return (value[:32] or "Satellite")

    def _load_device_id(self):
        path = os.path.join(self.config_dir, "device-id")
        try:
            with open(path, "r", encoding="ascii") as stream:
                device_id = stream.read().strip()
            if len(device_id) == 32 and all(c in "0123456789abcdef" for c in device_id):
                return device_id
        except OSError:
            pass

        device_id = uuid.uuid4().hex
        self._write_private_file(path, (device_id + "\n").encode("ascii"))
        return device_id

    @staticmethod
    def _valid_device_id(device_id):
        return isinstance(device_id, str) and DEVICE_ID_PATTERN.fullmatch(device_id) is not None

    @staticmethod
    def _write_private_file(path, data):
        temporary = path + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)

    def _load_or_create_certificate(self):
        try:
            with open(self._certificate_path, "rb") as stream:
                certificate = crypto.load_certificate(crypto.FILETYPE_PEM, stream.read())
            with open(self._private_key_path, "rb") as stream:
                private_key = crypto.load_privatekey(crypto.FILETYPE_PEM, stream.read())
            if certificate.get_subject().CN != self.device_id:
                raise ValueError("Local certificate identity does not match device ID")
            context = SSL.Context(SSL.TLS_METHOD)
            context.use_certificate(certificate)
            context.use_privatekey(private_key)
            context.check_privatekey()
            os.chmod(self._private_key_path, 0o600)
            os.chmod(self._certificate_path, 0o600)
            return certificate, private_key
        except (OSError, crypto.Error, ValueError):
            private_key = crypto.PKey()
            private_key.generate_key(crypto.TYPE_RSA, 2048)
            certificate = crypto.X509()
            certificate.set_version(2)
            certificate.set_serial_number(int.from_bytes(os.urandom(16), "big") or 1)
            certificate.get_subject().CN = self.device_id
            certificate.set_issuer(certificate.get_subject())
            certificate.set_pubkey(private_key)
            certificate.gmtime_adj_notBefore(0)
            certificate.gmtime_adj_notAfter(10 * 365 * 24 * 60 * 60)
            certificate.sign(private_key, "sha256")
            self._write_private_file(
                self._private_key_path,
                crypto.dump_privatekey(crypto.FILETYPE_PEM, private_key),
            )
            self._write_private_file(
                self._certificate_path,
                crypto.dump_certificate(crypto.FILETYPE_PEM, certificate),
            )
            return certificate, private_key

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="satellite-discovery", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            links = list(self._active.values())
            timers = list(self._pair_timers.values())
            reconnect_timers = list(self._reconnect_timers.values())
        for link in links:
            try:
                link["socket"].close()
            except OSError:
                pass
        for listener in (self._udp, self._tcp):
            if listener is not None:
                try:
                    listener.close()
                except OSError:
                    pass
        for timer in timers:
            timer.cancel()
        for timer in reconnect_timers:
            timer.cancel()
        if self._thread:
            self._thread.join(timeout=2)

    def devices(self):
        with self._lock:
            return copy.deepcopy(list(self._devices.values()))

    def refresh(self):
        self._refresh_requested.set()

    def _connect_known_devices(self):
        with self._lock:
            devices = [dict(device) for device in self._devices.values() if not device.get("secure")]
            for device in devices:
                self._last_connect_attempt.pop(device["id"], None)
        for device in devices:
            self._cancel_reconnect(device["id"])
            self._connect_to_device(device)

    def _notify_devices_changed(self):
        self.on_devices_changed(self.devices())

    def _identity_packet(self, include_port=True):
        body = {
            "deviceId": self.device_id,
            "protocolVersion": 8,
            "deviceName": self.device_name,
            "deviceType": "desktop",
            "incomingCapabilities": self.plugins.incoming_capabilities,
            "outgoingCapabilities": self.plugins.outgoing_capabilities,
        }
        if include_port:
            body["tcpPort"] = self.tcp_port
        return {"id": int(time.time() * 1000), "type": "kdeconnect.identity", "body": body}

    def _run(self):
        try:
            self._open_sockets()
            next_broadcast = 0
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_broadcast or self._refresh_requested.is_set():
                    refreshing = self._refresh_requested.is_set()
                    self._refresh_requested.clear()
                    self._broadcast()
                    if refreshing:
                        self._connect_known_devices()
                    next_broadcast = now + DISCOVERY_INTERVAL

                readable, _, _ = select.select([self._udp, self._tcp], [], [], 0.25)
                if self._udp in readable:
                    self._receive_discovery()
                if self._tcp in readable:
                    self._accept_connection()
                self._expire_devices()
        except (OSError, ValueError) as error:
            if not self._stop.is_set():
                print("Satellite discovery stopped: {}".format(error))
        finally:
            for listener in (self._udp, self._tcp):
                if listener is not None:
                    try:
                        listener.close()
                    except OSError:
                        pass

    def _open_sockets(self):
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._udp.bind(("", UDP_PORT))

        for port in TCP_PORTS:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("", port))
                listener.listen(16)
                self._tcp = listener
                self.tcp_port = port
                return
            except OSError:
                listener.close()
        raise OSError("No KDE Connect TCP port is available (1716–1764)")

    def _broadcast(self):
        payload = json.dumps(self._identity_packet(), separators=(",", ":")).encode("utf-8")
        sent = False
        for _, interface_name in socket.if_nameindex():
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                request = struct.pack("256s", interface_name[:15].encode("ascii"))
                local_address = socket.inet_ntoa(
                    fcntl.ioctl(sender.fileno(), 0x8915, request)[20:24]
                )
                if local_address.startswith("127."):
                    continue
                try:
                    broadcast_address = socket.inet_ntoa(
                        fcntl.ioctl(sender.fileno(), 0x8919, request)[20:24]
                    )
                except OSError:
                    broadcast_address = "255.255.255.255"
                if broadcast_address == "0.0.0.0":
                    broadcast_address = "255.255.255.255"
                sender.bind((local_address, 0))
                sender.sendto(payload, (broadcast_address, UDP_PORT))
                sent = True
            except OSError:
                continue
            finally:
                sender.close()
        if not sent:
            self._udp.sendto(payload, ("255.255.255.255", UDP_PORT))

    def _receive_discovery(self):
        payload, address = self._udp.recvfrom(MAX_PACKET_SIZE)
        try:
            packet = json.loads(payload.decode("utf-8"))
            if packet.get("type") != "kdeconnect.identity":
                return
            body = packet.get("body", {})
            device_id = body.get("deviceId")
            name = body.get("deviceName")
            version = int(body.get("protocolVersion", 0))
            port = int(body.get("tcpPort", 0))
            if (
                not self._valid_device_id(device_id)
                or device_id == self.device_id
                or version < 8
                or port not in TCP_PORTS
            ):
                return
            if not isinstance(name, str) or not name:
                name = device_id
            name = self._clean_device_name(name)
            device_type = body.get("deviceType", "desktop")
        except (UnicodeDecodeError, ValueError, TypeError, AttributeError):
            return

        with self._lock:
            old = self._devices.get(device_id, {})
            if not old and len(self._devices) >= MAX_TRACKED_DEVICES:
                return
            updated = dict(old)
            updated.update({
                "id": device_id,
                "name": name,
                "type": device_type,
                "address": address[0],
                "port": port,
                "last_seen": time.monotonic(),
                "connected": bool(old.get("connected")),
                "secure": bool(old.get("secure")),
                "paired": self._has_trusted_certificate(device_id),
                "plugins": list(old.get("plugins", [])),
            })
            changed = any(updated.get(k) != old.get(k) for k in ("name", "type", "address", "port", "paired"))
            self._devices[device_id] = updated
        if changed:
            self._notify_devices_changed()
        self._connect_to_device(dict(updated))

    def _connect_to_device(self, device):
        device_id = device["id"]
        now = time.monotonic()
        if not self._connection_slots.acquire(blocking=False):
            return
        with self._lock:
            active = self._active.get(device_id)
            if active or device_id in self._connecting:
                self._connection_slots.release()
                return
            if now - self._last_connect_attempt.get(device_id, 0) < 5:
                self._connection_slots.release()
                return
            self._last_connect_attempt[device_id] = now
            self._connecting.add(device_id)
        thread = threading.Thread(
            target=self._connect_outgoing,
            args=(device,),
            name="satellite-connect-{}".format(device_id[:8]),
            daemon=True,
        )
        thread.start()

    def _accept_connection(self):
        raw_socket, address = self._tcp.accept()
        self._configure_tcp_socket(raw_socket)
        if not self._connection_slots.acquire(blocking=False):
            raw_socket.close()
            return
        threading.Thread(
            target=self._handle_incoming_with_slot,
            args=(raw_socket, address),
            name="satellite-incoming",
            daemon=True,
        ).start()

    def _handle_incoming_with_slot(self, raw_socket, address):
        try:
            self._handle_incoming(raw_socket, address)
        finally:
            self._connection_slots.release()

    def _connection_packet(self, device_id):
        return _json_packet("kdeconnect.identity", {
            "deviceId": self.device_id,
            "protocolVersion": 8,
            "targetDeviceId": device_id,
            "targetProtocolVersion": 8,
        })

    def _connect_outgoing(self, device):
        raw_socket = None
        failed = False
        try:
            raw_socket = socket.create_connection((device["address"], device["port"]), timeout=8)
            self._configure_tcp_socket(raw_socket)
            raw_socket.settimeout(8)
            raw_socket.sendall(self._connection_packet(device["id"]))
            self._finish_tls_handshake(raw_socket, device, outgoing=True)
        except Exception as error:
            failed = True
            self._recover_connection_error(device.get("address"))
            if raw_socket is not None:
                raw_socket.close()
            with self._lock:
                link_is_active = self._active.get(device["id"]) is not None
            if not self._stop.is_set() and not link_is_active:
                print(
                    "KDE Connect handshake with {} failed: {} {!r} args={!r}".format(
                        device.get("name", device["id"]),
                        type(error).__name__,
                        error,
                        getattr(error, "args", ()),
                    ),
                    flush=True,
                )
        finally:
            with self._lock:
                self._connecting.discard(device["id"])
            self._connection_slots.release()
            if failed:
                self._schedule_reconnect(device["id"])

    def _recover_connection_error(self, address):
        """Ask the peer to try the reverse TCP connection, as KDE Connect does."""
        if not address or self._udp is None or self._stop.is_set():
            return
        payload = json.dumps(
            self._identity_packet(), separators=(",", ":")
        ).encode("utf-8")
        try:
            self._udp.sendto(payload, (address, UDP_PORT))
        except OSError:
            pass

    def _handle_incoming(self, raw_socket, address):
        try:
            raw_socket.settimeout(8)
            packet = _read_line(raw_socket)
            if packet.get("type") != "kdeconnect.identity":
                raise ValueError("Expected a KDE Connect connection identity")
            body = packet.get("body", {})
            device_id = body.get("deviceId")
            version = int(body.get("protocolVersion", 0))
            target_id = body.get("targetDeviceId")
            target_version = int(body.get("targetProtocolVersion", 8))
            if (
                not self._valid_device_id(device_id)
                or device_id == self.device_id
                or version < 8
                or target_id not in (None, "", self.device_id)
                or target_version < 8
            ):
                raise ValueError("Connection identity does not match this device")
            with self._lock:
                device = dict(self._devices.get(device_id, {}))
            device.update({"id": device_id, "address": address[0], "name": device.get("name", device_id), "type": device.get("type", "desktop")})
            self._finish_tls_handshake(raw_socket, device, outgoing=False)
        except Exception as error:
            raw_socket.close()
            if not self._stop.is_set():
                print(
                    "Incoming KDE Connect handshake failed: {} {!r} args={!r}".format(
                        type(error).__name__, error, getattr(error, "args", ())
                    ),
                    flush=True,
                )

    def _new_tls_connection(self, raw_socket, outgoing):
        context = SSL.Context(SSL.TLS_METHOD)
        context.use_certificate(self._certificate)
        context.use_privatekey(self._private_key)
        context.set_verify(
            SSL.VERIFY_PEER,
            lambda _connection, _certificate, error_number, _depth, verified: (
                bool(verified) or error_number in (18, 19)
            ),
        )
        connection = SSL.Connection(context, raw_socket)
        if outgoing:
            connection.set_accept_state()
        else:
            connection.set_connect_state()
        deadline = time.monotonic() + 8
        while True:
            try:
                connection.do_handshake()
                break
            except SSL.WantReadError:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([raw_socket], [], [], remaining)[0]:
                    raise TimeoutError("Timed out waiting to complete the TLS handshake")
            except SSL.WantWriteError:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [raw_socket], [], remaining)[1]:
                    raise TimeoutError("Timed out writing the TLS handshake")
        return connection

    @staticmethod
    def _certificate_device_id(certificate):
        for key, value in certificate.get_subject().get_components():
            if key == b"CN":
                return value.decode("utf-8", "replace")
        return None

    @staticmethod
    def _certificate_der(certificate):
        return crypto.dump_certificate(crypto.FILETYPE_ASN1, certificate)

    def _pairing_verification_code(self, peer_certificate, timestamp):
        public_keys = sorted((
            crypto.dump_publickey(crypto.FILETYPE_ASN1, self._certificate.get_pubkey()),
            crypto.dump_publickey(crypto.FILETYPE_ASN1, peer_certificate.get_pubkey()),
        ))
        digest = hashlib.sha256(
            public_keys[0] + public_keys[1] + str(int(timestamp)).encode("ascii")
        ).hexdigest()
        return digest[:8].upper()

    def _trusted_certificate_path(self, device_id):
        return os.path.join(self._trusted_dir, device_id + ".crt")

    def _has_trusted_certificate(self, device_id):
        try:
            with open(self._trusted_certificate_path(device_id), "rb") as stream:
                certificate = crypto.load_certificate(crypto.FILETYPE_PEM, stream.read())
            return self._certificate_device_id(certificate) == device_id
        except (OSError, crypto.Error):
            return False

    def _verify_pinned_certificate(self, device_id, peer_certificate):
        path = self._trusted_certificate_path(device_id)
        if not os.path.exists(path):
            return False
        with open(path, "rb") as stream:
            trusted = crypto.load_certificate(crypto.FILETYPE_PEM, stream.read())
        return self._certificate_der(trusted) == self._certificate_der(peer_certificate)

    def _save_trusted_certificate(self, device_id, certificate):
        path = self._trusted_certificate_path(device_id)
        self._write_private_file(
            path,
            crypto.dump_certificate(crypto.FILETYPE_PEM, certificate),
        )

    def _finish_tls_handshake(self, raw_socket, discovered_device, outgoing):
        raw_socket.settimeout(8)
        connection = self._new_tls_connection(raw_socket, outgoing)
        raw_socket.settimeout(None)
        peer_certificate = connection.get_peer_certificate()
        if peer_certificate is None:
            raise ValueError("Peer did not present a TLS certificate")
        device_id = discovered_device["id"]
        if self._certificate_device_id(peer_certificate) != device_id:
            raise ValueError("TLS certificate identity does not match the discovered device")
        trusted_path = self._trusted_certificate_path(device_id)
        pinned = self._verify_pinned_certificate(device_id, peer_certificate)
        if os.path.exists(trusted_path) and not pinned:
            raise ValueError("Trusted device certificate changed; refusing connection")

        identity = self._identity_packet(include_port=False)
        _send_all(connection, _json_packet(identity["type"], identity["body"]))
        remote_packet = _read_tls_line(connection, raw_socket)
        if remote_packet.get("type") != "kdeconnect.identity":
            raise ValueError("TLS peer did not send an identity packet")
        body = remote_packet.get("body", {})
        identity_device_id = body.get("deviceId")
        protocol_version = int(body.get("protocolVersion", 0))
        if identity_device_id != device_id or protocol_version < 8:
            raise ValueError("TLS identity does not match the discovered device")
        if self._certificate_device_id(peer_certificate) != identity_device_id:
            raise ValueError("TLS certificate identity does not match the device ID")

        device_name = body.get("deviceName")
        device_type = body.get("deviceType", "desktop")
        outgoing_capabilities = body.get("outgoingCapabilities", [])
        incoming_capabilities = body.get("incomingCapabilities", [])
        if not isinstance(device_name, str) or not device_name:
            raise ValueError("TLS identity has no device name")
        if not isinstance(outgoing_capabilities, list) or not isinstance(incoming_capabilities, list):
            raise ValueError("TLS identity has invalid capabilities")
        if any(not isinstance(item, str) for item in outgoing_capabilities + incoming_capabilities):
            raise ValueError("TLS identity has invalid capability names")

        link = {
            "id": device_id,
            "name": self._clean_device_name(device_name),
            "type": device_type,
            "address": raw_socket.getpeername()[0],
            "port": discovered_device.get("port", 0),
            "socket": raw_socket,
            "stream": connection,
            "certificate": peer_certificate,
            "paired": pinned,
            "outgoing": outgoing,
            "send_lock": threading.Lock(),
            "last_seen": time.monotonic(),
            "encryption": {
                "protocol": connection.get_protocol_version_name(),
                "cipher": connection.get_cipher_name(),
                "fingerprint": peer_certificate.digest("sha256").decode("ascii"),
            },
        }
        if not self._register_link(link, body, outgoing_capabilities, incoming_capabilities):
            raw_socket.close()
            return

        try:
            while not self._stop.is_set():
                if connection.pending() <= 0:
                    readable, _, _ = select.select([raw_socket], [], [], 0.5)
                    if not readable:
                        continue
                packet = _read_tls_line(connection, raw_socket, timeout=3600)
                self._handle_packet(link, packet)
        except (OSError, SSL.Error, ConnectionError, ValueError, json.JSONDecodeError) as error:
            with self._lock:
                link_is_active = self._active.get(link["id"]) is link
            if not self._stop.is_set() and link_is_active:
                print(
                    "KDE Connect link to {} closed: {} {!r} args={!r}".format(
                        link["name"],
                        type(error).__name__,
                        error,
                        getattr(error, "args", ()),
                    ),
                    flush=True,
                )
        finally:
            self._remove_link(link)

    @staticmethod
    def _configure_tcp_socket(raw_socket):
        """Apply the same TCP liveness settings KDE Connect uses for LAN links."""
        raw_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            raw_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
        if hasattr(socket, "TCP_KEEPINTVL"):
            raw_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        if hasattr(socket, "TCP_KEEPCNT"):
            raw_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        if hasattr(socket, "TCP_USER_TIMEOUT"):
            raw_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, 60000)

    def _register_link(self, link, identity, outgoing_capabilities, incoming_capabilities):
        device_id = link["id"]
        trusted_path = self._trusted_certificate_path(device_id)
        if os.path.exists(trusted_path) and not link["paired"]:
            return False
        with self._lock:
            previous = self._active.get(device_id)
            if previous:
                # KDE Connect replaces the active link with the newest
                # authenticated socket. Both endpoints must follow that same
                # socket when simultaneous/reverse connections occur.
                try:
                    previous["socket"].close()
                except OSError:
                    pass
            self._active[device_id] = link
            old = self._devices.get(device_id, {})
            available_plugins = self.plugins.available_for(
                device_id, incoming_capabilities, outgoing_capabilities
            )
            self._devices[device_id] = {
                **old,
                "id": device_id,
                "name": link["name"],
                "type": link["type"],
                "address": link["address"],
                "port": link["port"],
                "last_seen": time.monotonic(),
                "secure": True,
                "paired": link["paired"],
                "connected": link["paired"],
                "encryption": link["encryption"],
                "pair_requested": bool(old.get("pair_requested")),
                "pair_pending": bool(old.get("pair_pending")),
                "incomingCapabilities": incoming_capabilities,
                "outgoingCapabilities": outgoing_capabilities,
                "available_plugins": available_plugins,
                "plugins": [
                    plugin["name"] for plugin in available_plugins
                    if plugin["enabled"]
                ],
            }
        self._notify_devices_changed()
        self._cancel_reconnect(device_id)
        with self._lock:
            self._reconnect_attempts.pop(device_id, None)
        if link["paired"] and self.plugins.is_enabled_for(device_id, "mpris"):
            self.send_plugin_packet(
                "mpris",
                device_id,
                "kdeconnect.mpris.request",
                {"requestPlayerList": True},
            )
        return True

    def _handle_packet(self, link, packet):
        if not isinstance(packet, dict) or not isinstance(packet.get("body"), dict):
            return
        with self._lock:
            if self._active.get(link["id"]) is not link:
                return
        packet_type = packet.get("type")
        if packet_type == "kdeconnect.pair":
            self._handle_pair_packet(link, packet["body"])
        elif link["paired"]:
            self.plugins.handle_packet(self, link, packet)

    def _handle_pair_packet(self, link, body):
        pair_value = body.get("pair")
        if not isinstance(pair_value, bool):
            return
        device_id = link["id"]
        with self._lock:
            if self._active.get(device_id) is not link:
                return
        if pair_value:
            timestamp = body.get("timestamp")
            if timestamp is not None:
                try:
                    valid_time = abs(time.time() - float(timestamp)) <= PAIR_TIMESTAMP_WINDOW
                except (TypeError, ValueError):
                    valid_time = False
                if not valid_time:
                    return
                with self._lock:
                    device = self._devices.get(device_id)
                    if device:
                        device["pair_requested"] = True
                        device["pair_pending"] = False
                        device["pair_request_time"] = time.monotonic()
                        device["pair_timestamp"] = int(timestamp)
                        device["verification_code"] = self._pairing_verification_code(
                            link["certificate"], timestamp
                        )
                self._schedule_pair_timeout(device_id, incoming=True)
                self._notify_devices_changed()
            else:
                with self._lock:
                    device = self._devices.get(device_id)
                    pending = bool(device and device.get("pair_pending"))
                if pending:
                    self._complete_pairing(link)
        else:
            with self._lock:
                device = self._devices.get(device_id)
                if device:
                    device["pair_pending"] = False
                    device["pair_requested"] = False
                    device.pop("pair_request_time", None)
                    device.pop("pair_timestamp", None)
                    device.pop("verification_code", None)
            self._cancel_pair_timeout(device_id)
            self._notify_devices_changed()

    def request_pair(self, device_id):
        with self._lock:
            link = self._active.get(device_id)
            device = self._devices.get(device_id)
            if not link or not device or not device.get("secure") or device.get("paired"):
                return False
            device["pair_pending"] = True
            timestamp = int(time.time())
            device["pair_timestamp"] = timestamp
            device["verification_code"] = self._pairing_verification_code(
                link["certificate"], timestamp
            )
        self._schedule_pair_timeout(device_id, incoming=False)
        packet = _json_packet("kdeconnect.pair", {"pair": True, "timestamp": timestamp})
        try:
            self._send_packet(link, packet)
        except (OSError, SSL.Error):
            with self._lock:
                device = self._devices.get(device_id)
                if device:
                    device["pair_pending"] = False
                    device.pop("pair_timestamp", None)
                    device.pop("verification_code", None)
            self._cancel_pair_timeout(device_id)
            self._notify_devices_changed()
            return False
        self._notify_devices_changed()
        return True

    def send_ping(self, device_id, message=None):
        return self.plugins.send("ping", self, device_id, message=message)

    def ring_device(self, device_id):
        return self.plugins.send("ring", self, device_id)

    def send_plugin_packet(self, plugin_id, device_id, packet_type, body=None):
        plugin = self.plugins.plugins.get(plugin_id)
        if plugin is None or packet_type not in plugin.outgoing_capabilities:
            return False
        return self.plugins.send(
            plugin_id,
            self,
            device_id,
            packet_type=packet_type,
            body=body or {},
        )

    def inject_desktop_input(self, body):
        """Apply KDE Connect pointer and keyboard events in an X11 session."""
        if not self._xdotool:
            return False

        def run(*arguments):
            subprocess.run(
                [self._xdotool, *arguments],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )

        try:
            dx, dy = body.get("dx", 0), body.get("dy", 0)
            if (
                isinstance(dx, (int, float)) and not isinstance(dx, bool)
                and isinstance(dy, (int, float)) and not isinstance(dy, bool)
                and (dx or dy) and not body.get("scroll")
            ):
                run("mousemove_relative", "--sync", str(round(dx)), str(round(dy)))

            if body.get("scroll"):
                amount = max(-20, min(20, round(dy)))
                if amount:
                    run("click", "--repeat", str(abs(amount)), "--delay", "20", "4" if amount > 0 else "5")

            for field, command, button in (
                ("singleclick", "click", "1"),
                ("doubleclick", "click", "--repeat 2 --delay 100 1"),
                ("middleclick", "click", "2"),
                ("rightclick", "click", "3"),
                ("singlehold", "mousedown", "1"),
                ("singlerelease", "mouseup", "1"),
            ):
                if body.get(field):
                    run(command, *button.split())

            key = body.get("key")
            if isinstance(key, str) and key and len(key) <= 256:
                run("type", "--clearmodifiers", "--delay", "0", key)
            special = body.get("specialKey")
            key_names = {
                0: "BackSpace", 1: "Tab", 2: "Return", 3: "Left", 4: "Up",
                5: "Right", 6: "Down", 7: "Page_Up", 8: "Page_Down", 9: "Home",
                10: "End", 11: "KP_Enter", 12: "Delete", 13: "Escape",
                **{number: "F{}".format(number - 15) for number in range(16, 28)},
                28: "Caps_Lock", 29: "Num_Lock", 30: "Print", 31: "Pause", 32: "Menu",
            }
            if isinstance(special, int) and not isinstance(special, bool) and special in key_names:
                modifiers = [name for field, name in (("ctrl", "ctrl"), ("alt", "alt"), ("shift", "shift"), ("super", "super")) if body.get(field)]
                run("key", "+".join(modifiers + [key_names[special]]))
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def _send_plugin_packet(self, device_id, packet_type, body):
        with self._lock:
            link = self._active.get(device_id)
            device = self._devices.get(device_id)
            if (
                not link
                or not device
                or not device.get("paired")
                or packet_type not in device.get("incomingCapabilities", [])
                or not self.plugins.capability_enabled_for(device_id, packet_type)
            ):
                return False
        try:
            self._send_packet(link, _json_packet(packet_type, body))
        except (OSError, SSL.Error):
            return False
        return True

    def set_plugin_enabled(self, device_id, plugin_id, enabled):
        with self._lock:
            device = self._devices.get(device_id)
            if not device or not self.plugins.set_enabled_for(device_id, plugin_id, enabled):
                return False
            for plugin in device.get("available_plugins", []):
                if plugin["id"] == plugin_id:
                    plugin["enabled"] = bool(enabled)
            device["plugins"] = [
                plugin["name"] for plugin in device.get("available_plugins", [])
                if plugin.get("enabled")
            ]
        self._notify_devices_changed()
        return True

    def plugin_settings(self, device_id, plugin_id):
        return self.plugins.get_settings(device_id, plugin_id)

    def set_plugin_settings(self, device_id, plugin_id, settings):
        if not self.plugins.set_settings(device_id, plugin_id, settings):
            return False
        self._notify_devices_changed()
        return True

    def unpair(self, device_id):
        with self._lock:
            link = self._active.get(device_id)
            device = self._devices.get(device_id)
            if not link or not device or not device.get("paired"):
                return False
        try:
            self._send_packet(link, _json_packet("kdeconnect.pair", {"pair": False}))
            os.unlink(self._trusted_certificate_path(device_id))
        except (OSError, SSL.Error):
            return False
        link["paired"] = False
        self._cancel_reconnect(device_id)
        with self._lock:
            self._reconnect_attempts.pop(device_id, None)
        with self._lock:
            device = self._devices.get(device_id)
            if device:
                device["paired"] = False
                device["connected"] = False
                device.pop("battery", None)
                device.pop("isCharging", None)
                device.pop("last_ping", None)
        self._notify_devices_changed()
        return True

    def respond_pair(self, device_id, accept):
        with self._lock:
            link = self._active.get(device_id)
            device = self._devices.get(device_id)
            if not link or not device or not device.get("pair_requested"):
                return False
        try:
            self._send_packet(link, _json_packet("kdeconnect.pair", {"pair": bool(accept)}))
        except (OSError, SSL.Error):
            return False
        if accept:
            self._complete_pairing(link)
        else:
            with self._lock:
                device = self._devices.get(device_id)
                if device:
                    device["pair_requested"] = False
                    device.pop("pair_request_time", None)
                    device.pop("pair_timestamp", None)
                    device.pop("verification_code", None)
            self._cancel_pair_timeout(device_id)
            self._notify_devices_changed()
        return True

    def _send_packet(self, link, packet):
        with link["send_lock"]:
            _send_all(link["stream"], packet)

    def receive_share_payload(self, link, packet):
        """Fetch a file payload from the paired device's TLS payload port."""
        size = packet.get("payloadSize")
        transfer = packet.get("payloadTransferInfo", {})
        port = transfer.get("port") if isinstance(transfer, dict) else None
        if (
            isinstance(size, bool) or not isinstance(size, int)
            or size < 0 or size > 2 * 1024 * 1024 * 1024
            or isinstance(port, bool) or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            return None

        filename = os.path.basename(str(packet.get("body", {}).get("filename", "")))
        filename = "".join(c for c in filename if c.isprintable() and c not in "/\\")
        if filename in ("", ".", ".."):
            filename = "received-file"
        transfer_id = "{}:{}".format(link["id"], packet.get("id", time.monotonic_ns()))
        destination_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        stage = "creating the Downloads directory"
        last_progress_time = 0.0
        last_percent = -1

        def report_progress(received, finished=False, error=None):
            nonlocal last_progress_time, last_percent
            percent = min(100, int(received * 100 / size)) if size else 100
            now = time.monotonic()
            if (
                not finished
                and percent == last_percent
                and now - last_progress_time < 0.5
            ):
                return
            last_percent = percent
            last_progress_time = now
            self.on_transfer_progress({
                "id": transfer_id,
                "device_id": link["id"],
                "device_name": link["name"],
                "filename": filename,
                "received": received,
                "total": size,
                "percent": percent,
                "path": None,
                "finished": finished,
                "error": error,
            })

        try:
            os.makedirs(destination_dir, mode=0o700, exist_ok=True)
            destination = os.path.join(destination_dir, filename)
            stem, extension = os.path.splitext(filename)
            suffix = 1
            while os.path.exists(destination):
                destination = os.path.join(
                    destination_dir, "{} ({}){}".format(stem, suffix, extension)
                )
                suffix += 1

            report_progress(0)
            stage = "connecting to payload port {}".format(port)
            raw_socket = socket.create_connection((link["address"], port), timeout=12)
            raw_socket.settimeout(12)
            context = SSL.Context(SSL.TLS_CLIENT_METHOD)
            context.set_verify(SSL.VERIFY_NONE, lambda *_args: True)
            context.use_certificate(self._certificate)
            context.use_privatekey(self._private_key)
            connection = SSL.Connection(context, raw_socket)
            connection.set_connect_state()
            stage = "starting the payload TLS handshake"
            _tls_handshake(connection, raw_socket)
            peer_certificate = connection.get_peer_certificate()
            stage = "checking the payload device certificate"
            if not peer_certificate or not self._verify_pinned_certificate(
                link["id"], peer_certificate
            ):
                raise SSL.Error("Payload TLS certificate does not match paired device")

            temporary = destination + ".part"
            remaining = size
            stage = "receiving {} bytes".format(size)
            with open(temporary, "xb") as output:
                received = 0
                while remaining:
                    chunk = _recv_tls(connection, raw_socket, min(65536, remaining))
                    if not chunk:
                        raise ConnectionError("File payload ended early")
                    output.write(chunk)
                    remaining -= len(chunk)
                    received += len(chunk)
                    report_progress(received)
            stage = "saving the received file"
            os.replace(temporary, destination)
            try:
                connection.shutdown()
            except SSL.Error:
                pass
            connection.close()
            raw_socket.close()
            self.on_transfer_progress({
                "id": transfer_id,
                "device_id": link["id"],
                "device_name": link["name"],
                "filename": filename,
                "received": size,
                "total": size,
                "percent": 100,
                "path": destination,
                "finished": True,
                "error": None,
            })
            return destination
        except (OSError, SSL.Error, ConnectionError, ValueError) as error:
            self.on_transfer_progress({
                "id": transfer_id,
                "device_id": link["id"],
                "device_name": link["name"],
                "filename": filename,
                "received": size - locals().get("remaining", size),
                "total": size,
                "percent": last_percent,
                "path": None,
                "finished": True,
                "error": "{}: {!r} args={!r}".format(type(error).__name__, error, error.args),
            })
            print(
                "KDE Connect file receive from {} failed during {}: {} {!r} args={!r}".format(
                    link["name"], stage, type(error).__name__, error, error.args
                ),
                flush=True,
            )
            try:
                if "temporary" in locals() and os.path.exists(temporary):
                    os.unlink(temporary)
            except OSError:
                pass
            try:
                if "connection" in locals():
                    connection.close()
                if "raw_socket" in locals():
                    raw_socket.close()
            except OSError:
                pass
            return None

    def _complete_pairing(self, link):
        device_id = link["id"]
        with self._lock:
            if self._active.get(device_id) is not link:
                return
        self._save_trusted_certificate(device_id, link["certificate"])
        link["paired"] = True
        with self._lock:
            device = self._devices.get(device_id)
            if device:
                device["paired"] = True
                device["connected"] = True
                device["pair_requested"] = False
                device["pair_pending"] = False
                device.pop("pair_request_time", None)
                device.pop("pair_timestamp", None)
                device.pop("verification_code", None)
        self._cancel_pair_timeout(device_id)
        self._notify_devices_changed()

    def _schedule_pair_timeout(self, device_id, incoming):
        timer = threading.Timer(
            PAIR_TIMEOUT,
            self._expire_pair_request,
            args=(device_id, incoming),
        )
        timer.daemon = True
        with self._lock:
            previous = self._pair_timers.get(device_id)
            self._pair_timers[device_id] = timer
        if previous:
            previous.cancel()
        timer.start()

    def _cancel_pair_timeout(self, device_id):
        with self._lock:
            timer = self._pair_timers.pop(device_id, None)
        if timer:
            timer.cancel()

    def _schedule_reconnect(self, device_id):
        with self._lock:
            device = self._devices.get(device_id)
            if (
                self._stop.is_set()
                or not device
                or not device.get("paired")
                or self._active.get(device_id)
                or device_id in self._reconnect_timers
            ):
                return
            attempt = self._reconnect_attempts.get(device_id, 0)
            delay = min(6 * (2 ** attempt), 60)
            self._reconnect_attempts[device_id] = attempt + 1
            timer = threading.Timer(delay, self._retry_connection, args=(device_id,))
            timer.daemon = True
            self._reconnect_timers[device_id] = timer
        timer.start()

    def _retry_connection(self, device_id):
        with self._lock:
            if self._reconnect_timers.get(device_id) is not threading.current_thread():
                return
            self._reconnect_timers.pop(device_id, None)
            device = self._devices.get(device_id)
            if (
                self._stop.is_set()
                or not device
                or not device.get("paired")
                or self._active.get(device_id)
            ):
                return
            snapshot = dict(device)
        self._connect_to_device(snapshot)

    def _cancel_reconnect(self, device_id):
        with self._lock:
            timer = self._reconnect_timers.pop(device_id, None)
        if timer:
            timer.cancel()

    def _expire_pair_request(self, device_id, incoming):
        with self._lock:
            current_timer = threading.current_thread()
            if self._pair_timers.get(device_id) is not current_timer:
                return
            self._pair_timers.pop(device_id, None)
            device = self._devices.get(device_id)
            if not device:
                return
            key = "pair_requested" if incoming else "pair_pending"
            if not device.get(key):
                return
            device[key] = False
            device.pop("pair_request_time", None)
            device.pop("pair_timestamp", None)
            device.pop("verification_code", None)
        self._notify_devices_changed()

    def _remove_link(self, link):
        device_id = link["id"]
        removed_active_link = False
        with self._lock:
            if self._active.get(device_id) is link:
                del self._active[device_id]
                removed_active_link = True
                device = self._devices.get(device_id)
                if device:
                    device["secure"] = False
                    device["connected"] = False
                    device["pair_requested"] = False
                    device["pair_pending"] = False
                    device.pop("pair_request_time", None)
                    device.pop("pair_timestamp", None)
                    device.pop("verification_code", None)
        try:
            link["socket"].close()
        except OSError:
            pass
        if removed_active_link:
            self._cancel_pair_timeout(device_id)
            self._schedule_reconnect(device_id)
            self._notify_devices_changed()

    def _expire_devices(self):
        now = time.monotonic()
        with self._lock:
            expired = [
                device_id for device_id, device in self._devices.items()
                if now - device.get("last_seen", now) > DEVICE_TIMEOUT
                and not device.get("secure")
                and not device.get("paired")
                and not device.get("pair_requested")
            ]
            for device_id in expired:
                del self._devices[device_id]
        if expired:
            self._notify_devices_changed()
            with self._lock:
                for device_id in expired:
                    self._last_connect_attempt.pop(device_id, None)


def main():
    service = SatelliteDaemon()
    try:
        service.start()
        while service._thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()


if __name__ == "__main__":
    main()
