"""The main window: layout and wiring only. Each part lives in its own module."""

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from satellite.mpris_bridge import MprisBridge
from satellite.ui.clipboard_sync import ClipboardSync
from satellite.ui.context import ActionContext, BackgroundContext
from satellite.ui.custom_devices_dialog import CustomDevicesDialog
from satellite.ui.device_list import DeviceList
from satellite.ui.device_page import DevicePage
from satellite.ui.dialogs import ask_text, confirm, show_encryption_info, show_message
from satellite.ui.empty_state import EmptyState
from satellite.ui.plugin_config_dialog import run_plugin_config
from satellite.ui.transfer_tracker import TransferTracker
from satellite.i18n import _

NARROW_WIDTH = 640
SIGNATURE_KEYS = (
    "id", "name", "type", "connected", "secure", "paired", "pair_requested",
    "pair_pending", "verification_code", "battery", "isCharging",
    "plugins", "available_plugins", "incomingCapabilities", "plugin_data",
)


def device_signature(device):
    if not device:
        return None
    return tuple(
        (key, tuple(device.get(key, [])) if key == "plugins" else device.get(key))
        for key in SIGNATURE_KEYS
    )


class SatelliteWindow(Gtk.ApplicationWindow):
    def __init__(self, app, daemon, settings, notifier, tray_available=False):
        super().__init__(application=app, title=_("Satellite"))
        self.set_default_size(820, 540)
        self.daemon = daemon
        self.settings = settings
        self.notifier = notifier
        self._tray_available = tray_available
        self._current_id = None
        self._current_signature = None
        self._narrow = None
        self._showing_detail = False
        self.device_listeners = []  # called with the device list on every change

        self.transfers = TransferTracker(self, notifier)
        self.clipboard = ClipboardSync(daemon)
        try:
            self.media_bridge = MprisBridge(self._send_mpris_request)
        except (GLib.Error, RuntimeError) as error:
            self.media_bridge = None
            print("Could not register Satellite's MPRIS player: {}".format(error), flush=True)

        self._build_header()
        self._build_body()
        self._build_actions()
        self.connect("size-allocate", self._on_size_allocate)
        self.connect("delete-event", self._on_delete_event)
        self.connect("destroy", self._on_destroy)

        daemon.on_devices_changed = lambda devices: GLib.idle_add(self._refresh_devices, devices)
        daemon.on_transfer_progress = lambda progress: GLib.idle_add(self.transfers.update, progress)
        daemon.on_notify = notifier.notify
        daemon.on_close_notification = lambda nid: GLib.idle_add(notifier.close, nid)
        daemon.on_prompt_text = lambda *args: GLib.idle_add(self._prompt_text, *args)
        daemon.on_clipboard = lambda text: GLib.idle_add(self.clipboard.set_text, text)
        daemon.on_open_url = lambda url: GLib.idle_add(self._open_url, url)

        # Show the contents but not the window: the application presents it,
        # or keeps it hidden when starting minimized to the tray.
        self.get_titlebar().show_all()
        self.layout.show_all()
        # A Gtk.Stack can only show a child that is already visible.
        self.stack.set_visible_child_name("empty")
        self.back_button.hide()
        self._refresh_devices(daemon.devices())
        daemon.start()
        self.clipboard.start()

    # -- construction ------------------------------------------------------

    def _build_header(self):
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(_("Satellite"))
        self.set_titlebar(header)

        self.refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self.refresh_button.set_tooltip_text(_("Search for devices (F5)"))
        self.refresh_button.connect("clicked", lambda _b: self.refresh())
        header.pack_start(self.refresh_button)

        self.back_button = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        self.back_button.set_tooltip_text(_("Back to devices (Esc)"))
        self.back_button.connect("clicked", lambda _b: self.go_back())
        header.pack_start(self.back_button)

        menu = Gio.Menu()
        menu.append(_("Add devices by IP"), "win.add-devices")
        menu.append(_("Preferences"), "app.preferences")
        menu.append(_("About Satellite"), "app.about")
        quit_section = Gio.Menu()
        quit_section.append(_("Quit"), "app.quit")
        menu.append_section(None, quit_section)
        # A plain dropdown (a popover would point at the button with an arrow), and Satellite's
        # own hamburger: some themes, like Papirus, draw open-menu-symbolic as three dots.
        menu_button = Gtk.MenuButton()
        menu_button.set_use_popover(False)
        menu_button.set_image(Gtk.Image.new_from_icon_name("satellite-menu-symbolic", Gtk.IconSize.BUTTON))
        menu_button.set_tooltip_text(_("Menu"))
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

    def _build_body(self):
        self.layout = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.sidebar = DeviceList(on_refresh=self.refresh)
        self.sidebar.set_size_request(240, -1)
        self.sidebar.connect("device-selected", self._on_device_selected)
        self.sidebar.connect("selection-cleared", lambda _l: self._show_empty())
        self.layout.pack_start(self.sidebar, False, True, 0)
        self.divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.layout.pack_start(self.divider, False, False, 0)

        self.detail_scroller = Gtk.ScrolledWindow()
        self.detail_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.detail_scroller.set_shadow_type(Gtk.ShadowType.NONE)
        self.stack = Gtk.Stack()
        self.page = DevicePage(self)
        self.stack.add_named(self.page, "device")
        self.stack.add_named(EmptyState(
            "phone-symbolic", _("Select a device"),
            _("Pick a device on the left to see its status and change its plugins."),
        ), "empty")
        self.detail_scroller.add(self.stack)
        self.layout.pack_start(self.detail_scroller, True, True, 0)
        self.add(self.layout)

    def _build_actions(self):
        for name, callback, accels in (
            ("refresh", self.refresh, ["F5"]),
            ("back", self.go_back, ["Escape"]),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=callback: cb())
            self.add_action(action)
            self.get_application().set_accels_for_action("win." + name, accels)
        add_devices = Gio.SimpleAction.new("add-devices", None)
        add_devices.connect("activate", lambda *_a: self._show_custom_devices())
        self.add_action(add_devices)

    def _show_custom_devices(self):
        dialog = CustomDevicesDialog(self, self.daemon)
        dialog.run()
        dialog.destroy()

    # -- window behaviour ---------------------------------------------------

    def refresh(self):
        self.refresh_button.set_sensitive(False)
        self.daemon.refresh()
        GLib.timeout_add(2000, lambda: self.refresh_button.set_sensitive(True) and False)

    def go_back(self):
        if self._narrow and self._showing_detail:
            # Deselect so tapping the same device again reopens its page.
            self.sidebar.clear_selection()
            self._showing_detail = False
            self._update_layout()

    def _on_delete_event(self, *_args):
        if self.settings.get("background") and self._tray_available:
            self.hide()
            return True
        return False

    def _on_destroy(self, *_args):
        self.daemon.stop()
        self.transfers.clear()
        if self.media_bridge:
            self.media_bridge.close()

    def _on_size_allocate(self, _widget, allocation):
        narrow = allocation.width < NARROW_WIDTH
        if narrow != self._narrow:
            self._narrow = narrow
            if narrow:
                self._showing_detail = self._current_id is not None
            # GTK ignores layout changes made inside size-allocate, so defer.
            GLib.idle_add(self._update_layout)

    def _update_layout(self):
        self._apply_layout()
        self.layout.queue_resize()
        return GLib.SOURCE_REMOVE

    def _apply_layout(self):
        if not self._narrow:
            self.layout.set_orientation(Gtk.Orientation.HORIZONTAL)
            self.layout.set_child_packing(self.sidebar, False, True, 0, Gtk.PackType.START)
            self.sidebar.set_size_request(240, -1)
            for widget in (self.sidebar, self.divider, self.detail_scroller, self.refresh_button):
                widget.show()
            self.back_button.hide()
            return
        self.layout.set_orientation(Gtk.Orientation.VERTICAL)
        self.layout.set_child_packing(self.sidebar, True, True, 0, Gtk.PackType.START)
        self.sidebar.set_size_request(-1, -1)
        self.divider.hide()
        if self._showing_detail:
            self.sidebar.hide()
            self.detail_scroller.show()
            self.back_button.show()
            self.refresh_button.hide()
        else:
            self.sidebar.show()
            self.detail_scroller.hide()
            self.back_button.hide()
            self.refresh_button.show()

    # -- device updates -------------------------------------------------------

    def _refresh_devices(self, devices):
        for listener in self.device_listeners:
            listener(devices)
        if self.media_bridge:
            self.media_bridge.update(devices)
        self.clipboard.on_devices(devices)
        self.sidebar.update(devices)

        current = next((d for d in devices if d["id"] == self._current_id), None)
        if self._current_id and current is None:
            self._show_empty()
        elif current is not None and device_signature(current) != self._current_signature:
            self._show_device(current, preserve_scroll=True)
        self._update_layout()
        return GLib.SOURCE_REMOVE

    def _on_device_selected(self, _list, device):
        if self._narrow:
            self._showing_detail = True
        self._show_device(device)
        self._update_layout()

    def _show_device(self, device, preserve_scroll=False):
        adjustment = self.detail_scroller.get_vadjustment()
        position = adjustment.get_value() if preserve_scroll else 0
        self._current_id = device["id"]
        self._current_signature = device_signature(device)
        self.page.show_device(device)
        self.stack.set_visible_child_name("device")
        GLib.idle_add(self._restore_scroll, position)

    def _restore_scroll(self, position):
        adjustment = self.detail_scroller.get_vadjustment()
        maximum = max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(min(position, maximum))
        return GLib.SOURCE_REMOVE

    def _show_empty(self):
        self._current_id = None
        self._current_signature = None
        self._showing_detail = False
        self.stack.set_visible_child_name("empty")
        self._update_layout()

    def current_devices(self):
        return self.daemon.devices()

    def _device(self, device_id):
        devices = self.current_devices()
        return next((d for d in devices if d["id"] == device_id), None)

    # -- controller interface used by DevicePage ------------------------------

    def pair(self, device_id):
        if not self.daemon.request_pair(device_id):
            self._pairing_error()

    def accept_pair(self, device_id):
        if not self.daemon.respond_pair(device_id, True):
            self._pairing_error()

    def deny_pair(self, device_id):
        if not self.daemon.respond_pair(device_id, False):
            self._pairing_error()

    def unpair(self, device_id):
        device = self._device(device_id)
        if device and confirm(
            self, _("Unpair from {}?").format(device["name"]),
            _("This removes the trusted connection to this device."), _("Unpair"),
        ) and not self.daemon.unpair(device_id):
            self._pairing_error()

    def cancel_pair(self, device_id):
        self.daemon.cancel_pair(device_id)

    def show_encryption(self, device_id):
        device = self._device(device_id)
        if device:
            show_encryption_info(self, device)

    def toggle_plugin(self, device_id, plugin_id, state):
        return self.daemon.set_plugin_enabled(device_id, plugin_id, state)

    def configure_plugin(self, device_id, plugin_id):
        device = self._device(device_id)
        plugin = next((p for p in (device or {}).get("available_plugins", [])
                       if p["id"] == plugin_id), None)
        if not plugin:
            return
        settings = self.daemon.plugin_settings(device_id, plugin_id)
        context = ActionContext(self, self.daemon, device_id)
        handled, values = self.daemon.plugins.configure(plugin_id, context, settings)
        if not handled:
            notes = self.daemon.plugins.settings_notes(plugin_id, self.daemon, device_id)
            buttons = self.daemon.plugins.settings_buttons(plugin_id, context)
            values = run_plugin_config(self, plugin, settings, notes, buttons)
        if values is not None:
            self.daemon.set_plugin_settings(device_id, plugin_id, values)

    def run_action(self, device_id, action, background=False):
        """Run a plugin action. ``background`` reports problems as notifications."""
        context = BackgroundContext if background else ActionContext
        try:
            action.callback(context(self, self.daemon, device_id))
        except Exception as error:  # a plugin bug must not take the window down
            print("Action {} failed: {!r}".format(action.id, error), flush=True)
            context(self, self.daemon, device_id).error(_("{} failed").format(action.label), str(error))

    def open_device(self, device_id):
        """Show the window on this device's page (used by the tray)."""
        self.present()
        self.sidebar.select(device_id)

    # -- helpers ----------------------------------------------------------------

    def _prompt_text(self, title, label, callback):
        """A reply box that doesn't bring up the main window when it's hidden."""
        text = ask_text(self if self.get_visible() else None, title, label, accept_label=_("Send"))
        if text:
            callback(text)
        return GLib.SOURCE_REMOVE

    def _pairing_error(self):
        title = _("Could not exchange the pairing request")
        text = (_("The secure device connection is not available. Check that both devices are on "
                "the same network and that the KDE Connect ports are allowed through the firewall."))
        if self.get_visible():
            show_message(self, title, text)
        else:  # pairing from the tray: don't pop up the window just for this
            self.daemon.notify(title, text, icon="dialog-warning-symbolic")

    def _send_mpris_request(self, plugin_id, device_id, packet_type, body):
        if not device_id:
            return False
        return self.daemon.send_plugin_packet(plugin_id, device_id, packet_type, body)

    @staticmethod
    def _open_url(url):
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error as error:
            print("Could not open {}: {}".format(url, error), flush=True)
        return GLib.SOURCE_REMOVE
