"""Low-level KDE Connect packet and TLS stream helpers.

Shared by the daemon (links and pairing) and by plugins that open payload
connections (file transfer).
"""

import json
import select
import time

from OpenSSL import SSL

MAX_PACKET_SIZE = 8192
# Packets on a verified link, like the whole contact list in one packet, can be megabytes.
MAX_LINK_PACKET_SIZE = 64 * 1024 * 1024


def json_packet(packet_type, body, payload_size=None, payload_port=None):
    """Serialize one newline-terminated KDE Connect packet.

    ``payload_size`` and ``payload_port`` describe a follow-up TLS payload
    transfer (file sharing), which sits beside the body in the packet.
    """
    packet = {
        "id": int(time.time() * 1000),
        "type": packet_type,
        "body": body,
    }
    if payload_size is not None:
        packet["payloadSize"] = payload_size
        packet["payloadTransferInfo"] = {"port": payload_port}
    return (json.dumps(packet, separators=(",", ":")) + "\n").encode("utf-8")


def read_line(stream, limit=MAX_PACKET_SIZE):
    data = bytearray()
    while len(data) < limit:
        chunk = stream.recv(1)
        if not chunk:
            raise ConnectionError("Peer closed the connection")
        if chunk == b"\n":
            return json.loads(data.decode("utf-8"))
        data.extend(chunk)
    raise ValueError("KDE Connect packet exceeded the size limit")


def send_all(stream, data):
    sent = 0
    while sent < len(data):
        count = stream.send(data[sent:])
        if count <= 0:
            raise ConnectionError("Peer closed the connection while sending")
        sent += count


def read_tls_line(stream, raw_socket, limit=MAX_PACKET_SIZE, timeout=8):
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


def tls_handshake(stream, raw_socket, timeout=12):
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


def recv_tls(stream, raw_socket, size, timeout=30):
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


def send_tls(stream, raw_socket, data, timeout=30):
    """Send all of ``data`` over a non-blocking pyOpenSSL stream."""
    view = memoryview(data)
    deadline = time.monotonic() + timeout
    while len(view):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Timed out sending a payload TLS record")
        try:
            sent = stream.send(view[:65536].tobytes())
            view = view[sent:]
            deadline = time.monotonic() + timeout
        except SSL.WantWriteError:
            if not select.select([], [raw_socket], [], remaining)[1]:
                raise TimeoutError("Timed out sending a payload TLS record")
        except SSL.WantReadError:
            if not select.select([raw_socket], [], [], remaining)[0]:
                raise TimeoutError("Timed out sending a payload TLS record")
