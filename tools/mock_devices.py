"""Realistic fake devices for previewing the UI without a phone.

Used by tools/preview.py. Each entry mirrors what SatelliteDaemon.devices()
returns, with plugin lists computed from the real plugin manager.
"""

PHONE_ACCEPTS = [
    "kdeconnect.ping", "kdeconnect.share.request", "kdeconnect.clipboard",
    "kdeconnect.clipboard.connect", "kdeconnect.findmyphone.request",
    "kdeconnect.runcommand", "kdeconnect.runcommand.output", "kdeconnect.battery",
    "kdeconnect.notification.request", "kdeconnect.notification.action",
    "kdeconnect.notification.reply", "kdeconnect.mpris.request",
    "kdeconnect.mousepad.echo", "kdeconnect.mousepad.keyboardstate",
    "kdeconnect.lock.request", "kdeconnect.lock", "kdeconnect.telephony.request_mute",
    "kdeconnect.sftp.request", "kdeconnect.connectivity_report.request",
]
PHONE_SENDS = [
    "kdeconnect.ping", "kdeconnect.share.request", "kdeconnect.clipboard",
    "kdeconnect.clipboard.connect", "kdeconnect.findmyphone.request",
    "kdeconnect.runcommand.request", "kdeconnect.battery", "kdeconnect.notification",
    "kdeconnect.mpris", "kdeconnect.mousepad.request", "kdeconnect.lock",
    "kdeconnect.lock.request", "kdeconnect.telephony", "kdeconnect.sftp",
    "kdeconnect.connectivity_report", "kdeconnect.presenter", "kdeconnect.contacts.response_uids_timestamps",
    "kdeconnect.sms.messages", "kdeconnect.systemvolume",
]

ENCRYPTION = {
    "protocol": "TLSv1.3", "cipher": "TLS_AES_256_GCM_SHA384",
    "fingerprint": "9F:2C:41:D8:07:B3:6E:55:AA:10:C4:3D:F2:81:0B:7A",
}


def _device(manager, device_id, name, kind, **fields):
    plugins = manager.available_for(device_id, PHONE_ACCEPTS, PHONE_SENDS)
    base = {
        "id": device_id, "name": name, "type": kind,
        "connected": False, "secure": False, "paired": False,
        "pair_requested": False, "pair_pending": False,
        "incomingCapabilities": list(PHONE_ACCEPTS),
        "available_plugins": plugins,
        "plugins": [p["name"] for p in plugins],
        "plugin_data": {}, "encryption": ENCRYPTION,
    }
    base.update(fields)
    return base


def build(manager):
    return [
        _device(
            manager, "a1" * 16, "Pixel 7", "phone",
            connected=True, secure=True, paired=True, battery=82, isCharging=True,
            last_ping="Ping from Pixel 7",
            plugin_data={
                "mpris": {"type": "kdeconnect.mpris", "id": 1, "body": {
                    "player": "Spotify", "title": "Comfortably Numb",
                    "artist": "Pink Floyd", "isPlaying": True}},
                "connectivity_report": {"type": "kdeconnect.connectivity_report", "id": 2, "body": {
                    "signalStrengths": {"0": {"networkType": "LTE", "signalStrength": 3}}}},
                "lock": {"type": "kdeconnect.lock", "id": 3, "body": {"isLocked": False}},
            },
        ),
        _device(
            manager, "b2" * 16, "Galaxy Tab S8", "tablet",
            secure=False, paired=True, battery=41,
        ),
        _device(
            manager, "c3" * 16, "Moto G Power", "phone",
            secure=True, pair_requested=True, verification_code="4F3A 91C2",
        ),
        _device(manager, "d4" * 16, "Surface Pro 9", "tablet", secure=True),
    ]
