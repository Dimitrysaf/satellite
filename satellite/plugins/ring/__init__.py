"""Find my device: ring the phone, or let the phone ring this computer."""

import os
import shutil
import subprocess
import threading

from satellite.plugins._base import PluginAction

ENABLED_BY_DEFAULT = True
RING_SECONDS = 30
FALLBACK_SOUND = "/usr/share/sounds/freedesktop/stereo/phone-incoming-call.oga"


def find_sound_command():
    """Return an argv that plays one ring, or None if no player exists."""
    if shutil.which("canberra-gtk-play"):
        return ["canberra-gtk-play", "-i", "phone-incoming-call", "-d", "Satellite"]
    if shutil.which("paplay") and os.path.exists(FALLBACK_SOUND):
        return ["paplay", FALLBACK_SOUND]
    return None


class Plugin:
    plugin_id = "ring"
    display_name = "Find my device"
    description = "Ring the paired device, or ring this computer when the device asks."
    icon = "phone-ringing-symbolic"
    incoming_capabilities = ("kdeconnect.findmyphone.request",)
    outgoing_capabilities = ("kdeconnect.findmyphone.request",)

    def __init__(self, sound_command=find_sound_command, runner=subprocess.run):
        self._sound_command = sound_command
        self._runner = runner
        self._ringing = {}
        self._lock = threading.Lock()

    @staticmethod
    def is_supported(remote_incoming, remote_outgoing):
        capability = "kdeconnect.findmyphone.request"
        return capability in remote_incoming or capability in remote_outgoing

    def handle_packet(self, daemon, link, packet):
        if packet.get("type") != "kdeconnect.findmyphone.request":
            return False
        with self._lock:
            already = link["id"] in self._ringing
        if already:
            self.stop(daemon, link["id"])
        else:
            self._start(daemon, link)
        return True

    def stop(self, daemon, device_id):
        with self._lock:
            event = self._ringing.pop(device_id, None)
        if event:
            event.set()
        with daemon._lock:
            device = daemon._devices.get(device_id)
            if device:
                device["ring_requested"] = False
        daemon._notify_devices_changed()

    def _start(self, daemon, link):
        device_id = link["id"]
        stop_event = threading.Event()
        with self._lock:
            self._ringing[device_id] = stop_event
        with daemon._lock:
            device = daemon._devices.get(device_id)
            if device:
                device["ring_requested"] = True
        daemon._notify_devices_changed()
        daemon.notify(
            "{} is looking for this computer".format(link["name"]),
            "Satellite is playing a sound so you can find it.",
            icon="phone-ringing-symbolic",
            actions=[("stop", "Stop", lambda: self.stop(daemon, device_id))],
            timeout=RING_SECONDS * 1000,
        )
        threading.Thread(
            target=self._play, args=(daemon, device_id, stop_event), daemon=True
        ).start()

    def _play(self, daemon, device_id, stop_event):
        command = self._sound_command()
        waited = 0.0
        while command and not stop_event.is_set() and waited < RING_SECONDS:
            try:
                self._runner(
                    command, check=False, timeout=10,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError):
                break
            waited += 1.0
            stop_event.wait(0.3)
        if not stop_event.is_set():
            stop_event.wait(0 if command else RING_SECONDS)
            self.stop(daemon, device_id)

    @staticmethod
    def send(daemon, device_id, **_kwargs):
        return daemon._send_plugin_packet(device_id, "kdeconnect.findmyphone.request", {})

    def actions(self, device):
        def run(ctx):
            if not ctx.daemon.ring_device(ctx.device_id):
                ctx.error(
                    "Could not ring device",
                    "The secure connection is unavailable or the device does not support Find my device.",
                )

        return [PluginAction("ring", "Find device", "audio-volume-high-symbolic", run, "Make the device ring")]
