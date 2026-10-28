"""Find my device: ring the phone, or let the phone ring this computer."""

import os
import shutil
import subprocess
import threading

from satellite.plugins._base import PluginAction
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
RING_SECONDS = 30
FALLBACK_SOUND = "/usr/share/sounds/freedesktop/stereo/phone-incoming-call.oga"


def find_sound_command(sound=""):
    """Return an argv that plays one ring, or None if no player exists.

    ``sound`` is an optional audio file chosen in the plugin's settings.
    """
    if sound and os.path.isfile(sound):
        if shutil.which("paplay"):
            return ["paplay", sound]
        if shutil.which("canberra-gtk-play"):
            return ["canberra-gtk-play", "-f", sound, "-d", "Satellite"]
    if shutil.which("canberra-gtk-play"):
        return ["canberra-gtk-play", "-i", "phone-incoming-call", "-d", "Satellite"]
    if shutil.which("paplay") and os.path.exists(FALLBACK_SOUND):
        return ["paplay", FALLBACK_SOUND]
    return None


class Plugin:
    plugin_id = "ring"
    display_name = _("Find my device")
    description = _("Ring the paired device, or ring this computer when the device asks.")
    icon = "find-location-symbolic"
    incoming_capabilities = ("kdeconnect.findmyphone.request",)
    outgoing_capabilities = ("kdeconnect.findmyphone.request",)
    settings_schema = (
        {"key": "sound", "label": _("Sound to play"), "type": "file", "default": ""},
    )

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
            _("{} is looking for this computer").format(link["name"]),
            _("Satellite is playing a sound so you can find it."),
            icon="find-location-symbolic",
            actions=[("stop", _("Stop"), lambda: self.stop(daemon, device_id))],
            timeout=RING_SECONDS * 1000,
        )
        threading.Thread(
            target=self._play, args=(daemon, device_id, stop_event), daemon=True
        ).start()

    def _play(self, daemon, device_id, stop_event):
        sound = daemon.plugins.get_settings(device_id, self.plugin_id).get("sound", "")
        command = self._sound_command(sound)
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
                    _("Could not ring device"),
                    _("The secure connection is unavailable or the device does not support Find my device."),
                )

        return [PluginAction("ring", _("Find device"), "audio-volume-high-symbolic", run, _("Make the device ring"))]

    # -- configuration dialog (GTK is imported lazily; the daemon is headless)

    def configure(self, ctx, settings):
        """Choose the sound played when the device looks for this computer."""
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dialog = Gtk.Dialog(title=_("Find my device"), transient_for=ctx.parent, modal=True)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Save"), Gtk.ResponseType.ACCEPT)
        grid = Gtk.Grid(column_spacing=8, row_spacing=10)
        grid.set_border_width(12)
        dialog.get_content_area().add(grid)

        intro = Gtk.Label(
            label=_("Sound to play when this device looks for this computer."), xalign=0, wrap=True)
        intro.get_style_context().add_class("dim-label")
        grid.attach(intro, 0, 0, 3, 1)

        chooser = Gtk.FileChooserButton(title=_("Choose a sound"), action=Gtk.FileChooserAction.OPEN)
        chooser.set_hexpand(True)
        audio = Gtk.FileFilter()
        audio.set_name("Audio files")
        audio.add_mime_type("audio/*")
        chooser.add_filter(audio)
        if settings.get("sound"):
            chooser.set_filename(settings["sound"])
        grid.attach(chooser, 0, 1, 1, 1)

        def play(_button):
            command = self._sound_command(chooser.get_filename() or "")
            if command:
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        play_button = Gtk.Button.new_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.BUTTON)
        play_button.set_tooltip_text(_("Play"))
        play_button.connect("clicked", play)
        grid.attach(play_button, 1, 1, 1, 1)

        reset = Gtk.Button.new_from_icon_name("edit-clear-symbolic", Gtk.IconSize.BUTTON)
        reset.set_tooltip_text(_("Use the default sound"))
        reset.connect("clicked", lambda _b: chooser.unselect_all())
        grid.attach(reset, 2, 1, 1, 1)

        dialog.show_all()
        accepted = dialog.run() == Gtk.ResponseType.ACCEPT
        sound = chooser.get_filename() or ""
        dialog.destroy()
        return {"sound": sound} if accepted else None
