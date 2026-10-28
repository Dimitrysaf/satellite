"""The Satellite Messages window."""

import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from satellite.vcard import Directory
from satellite_sms import google_messages
from satellite_sms.conversation_list import ConversationList
from satellite_sms.model import People, Store, address_set
from satellite_sms.settings import MessagesSettings
from satellite_sms.thread_view import Composer, RecipientBar, ThreadView, install_css
from satellite.i18n import _

NARROW_WIDTH = 640
PAGE = 50  # messages fetched at a time when scrolling back

GOOGLE_WARNING = _("""Messages will be shown by Google's website instead of by Satellite.

• Satellite can't see, check or control what Google shows or does in this view.
• Messages go through Google's servers and the internet, not only your local network. Your phone needs internet access, and Google Messages must be its messaging app.
• Google's terms and privacy policy apply. Google can change or stop this service, or stop it from working here.
• The pairing stays on this computer until you sign out, so anyone using this computer account can read your messages.
• Only messages.google.com opens here; links in messages open in your web browser. Camera, microphone, location and website notifications are blocked.
• To unpair later, sign out from this menu, or on the phone open Google Messages → Device pairing.""")


class MessagesWindow(Gtk.ApplicationWindow):
    def __init__(self, app, client):
        super().__init__(application=app, title=_("Messages"))
        install_css()
        self.set_default_size(900, 620)
        self.client = client
        self.device = None
        self.devices = []
        self.store = Store()
        self.directory = None
        self.people = People()
        self.thread_id = None
        self.pending = []          # messages being sent, shown until the phone echoes them
        self.pending_new = None    # address set of a new conversation awaiting its thread
        self._wanted_attachments = set()
        self._narrow = None
        self._showing_detail = False
        self._requested = None     # (device, number) from the command line
        self.settings = MessagesSettings()
        self.google_view = None

        self._build_header()
        self._build_body()
        self.connect("size-allocate", self._on_size_allocate)

        client.connect("available", lambda _c, up: self._on_available(up))
        client.connect("devices-changed", lambda _c: self._load_devices())
        client.connect("messages-received", lambda _c, device, messages: self._on_messages(device, messages))
        client.connect("attachment-received", lambda _c, device, uid, path: self._on_attachment(uid, path))
        client.connect("contacts-changed", lambda _c, device: self._load_contacts())

    # -- construction --------------------------------------------------------

    def _build_header(self):
        header = Gtk.HeaderBar(show_close_button=True, title=_("Messages"))
        self.set_titlebar(header)
        self.header = header
        self.back_button = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        self.back_button.set_tooltip_text(_("Back to conversations"))
        self.back_button.connect("clicked", lambda _b: self._go_back())
        header.pack_start(self.back_button)
        self.new_button = Gtk.Button.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
        self.new_button.set_tooltip_text(_("New message (Ctrl+N)"))
        self.new_button.connect("clicked", lambda _b: self.new_message())
        header.pack_start(self.new_button)

        menu = Gio.Menu()
        menu.append(_("Use Google Messages for web"), "win.google-messages")
        menu.append(_("Sign out of Google Messages…"), "win.google-sign-out")
        menu_button = Gtk.MenuButton(use_popover=True, menu_model=menu)
        menu_button.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        header.pack_end(menu_button)
        self.refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self.refresh_button.set_tooltip_text(_("Load messages from the phone (F5)"))
        self.refresh_button.connect("clicked", lambda _b: self.refresh())
        header.pack_end(self.refresh_button)

        self.google_action = Gio.SimpleAction.new_stateful(
            "google-messages", None, GLib.Variant("b", self.settings.get("google_messages")))
        self.google_action.connect("change-state", self._on_google_toggled)
        self.add_action(self.google_action)
        self.sign_out_action = Gio.SimpleAction.new("google-sign-out", None)
        self.sign_out_action.connect("activate", lambda *_a: self._sign_out())
        self.add_action(self.sign_out_action)

        for name, callback, accels in (("new", self.new_message, ["<Primary>n"]),
                                       ("refresh", self.refresh, ["F5"])):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=callback: cb())
            self.add_action(action)
            self.get_application().set_accels_for_action("win." + name, accels)

    def _build_body(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.infobar = Gtk.InfoBar(show_close_button=False)
        self.info_label = Gtk.Label(xalign=0, wrap=True)
        self.infobar.get_content_area().add(self.info_label)
        self.info_button = self.infobar.add_button(_("Start Satellite"), 1)
        self.infobar.connect("response", lambda _bar, _response: self.client.start_satellite())
        outer.pack_start(self.infobar, False, False, 0)

        self.layout = Gtk.Box()
        self.list = ConversationList()
        self.list.set_size_request(300, -1)
        # Only shown with more than one phone; kept out of the header bar so
        # narrow windows stay possible.
        self.device_combo = Gtk.ComboBoxText()
        self.device_combo.set_tooltip_text(_("Phone"))
        self.device_combo.set_margin_start(8)
        self.device_combo.set_margin_end(8)
        self.device_combo.set_margin_top(8)
        self.device_combo.connect("changed", self._on_device_chosen)
        self.list.pack_start(self.device_combo, False, False, 0)
        self.list.reorder_child(self.device_combo, 0)
        self.list.connect("selected", lambda _l, row: self.open_thread(row.thread_id))
        self.layout.pack_start(self.list, False, True, 0)
        self.divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.layout.pack_start(self.divider, False, False, 0)

        self.stack = Gtk.Stack(hhomogeneous=False)  # size to the visible page only
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        empty.pack_start(Gtk.Image.new_from_icon_name("chat-message-new-symbolic", Gtk.IconSize.DIALOG),
                         False, False, 0)
        self.empty_label = Gtk.Label(label=_("Pick a conversation, or start a new one."))
        self.empty_label.get_style_context().add_class("dim-label")
        empty.pack_start(self.empty_label, False, False, 0)
        self.stack.add_named(empty, "empty")

        self.thread_view = ThreadView(self)
        self.thread_view.connect("send", lambda _v, text, files: self._send(text, files))
        self.thread_view.connect("load-older", lambda _v: self._load_older())
        self.thread_view.connect("open-attachment", lambda _v, a: self._open_attachment(a))
        self.stack.add_named(self.thread_view, "thread")

        new = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.recipients = RecipientBar()
        self.recipients.namer = lambda number: self.people.name(number)
        new.pack_start(self.recipients, False, False, 0)
        new.pack_start(Gtk.Separator(), False, False, 0)
        hint = Gtk.Label(label=_("Add one person for a text message, or several for a group message."),
                         wrap=True, valign=Gtk.Align.CENTER)
        hint.get_style_context().add_class("dim-label")
        new.pack_start(hint, True, True, 0)
        self.new_composer = Composer(self)
        self.new_composer.connect("send", lambda _c, text, files: self._send_new(text, files))
        new.pack_start(self.new_composer, False, False, 0)
        self.stack.add_named(new, "new")

        self.layout.pack_start(self.stack, True, True, 0)
        outer.pack_start(self.layout, True, True, 0)
        # Satellite's own view, or Google Messages for web in its place.
        self.modes = Gtk.Stack()
        self.modes.add_named(outer, "satellite")
        self.add(self.modes)
        self.modes.show()
        outer.show_all()
        self.header.show_all()
        self.infobar.hide()
        self.stack.set_visible_child_name("empty")
        self.back_button.hide()

    # -- devices -----------------------------------------------------------------

    def start(self, device=None, to=None):
        """Called on launch and when launched again with --device / --to."""
        self._requested = (device, to)
        if self._apply_mode():
            return  # Google's page needs nothing from Satellite
        if self.client.available:
            self._on_available(True)
        elif not self.client.start_satellite():
            self._on_available(False)

    def _on_available(self, available):
        if not available:
            self._inform(_("Satellite is not running, so messages can't be loaded."), button=True)
            return
        self._load_devices()

    def _load_devices(self):
        devices = [d for d in self.client.devices() if d.get("sms") and d.get("paired")]
        self.devices = devices
        wanted, to = self._requested or (None, None)
        current = wanted if any(d["id"] == wanted for d in devices) else self.device
        if not any(d["id"] == current for d in devices):
            connected = [d for d in devices if d["connected"]]
            current = (connected or devices or [{"id": None}])[0]["id"]
        self.device_combo.handler_block_by_func(self._on_device_chosen)
        self.device_combo.remove_all()
        for device in devices:
            self.device_combo.append(device["id"], device["name"])
        if current:
            self.device_combo.set_active_id(current)
        self.device_combo.handler_unblock_by_func(self._on_device_chosen)
        self.device_combo.set_visible(len(devices) > 1)
        if current != self.device:
            self._select_device(current)
        self._update_info()
        if to and current:
            self._requested = None
            self.new_message([to])

    def _on_device_chosen(self, combo):
        if combo.get_active_id() and combo.get_active_id() != self.device:
            self._select_device(combo.get_active_id())

    def _select_device(self, device_id):
        self.device = device_id
        self.store.clear()
        self.pending = []
        self.thread_id = None
        self.stack.set_visible_child_name("empty")
        device = self._device()
        self.header.set_subtitle(device["name"] if device else None)
        if device_id is None:
            self.list.set_conversations([], self.people)
            return
        self._load_contacts()
        self.store.add(self.client.conversations(device_id))
        self._refresh_list()
        self.client.request_conversations(device_id)

    def _device(self):
        return next((d for d in self.devices if d["id"] == self.device), None)

    def _update_info(self):
        device = self._device()
        if not self.devices:
            self._inform(_("No paired phone with Messages switched on. Pair a phone in Satellite first."))
        elif device and not device["connected"]:
            self._inform(_("{} is not connected. New messages appear when it reconnects.").format(device["name"]))
        else:
            self.infobar.hide()

    def _inform(self, text, button=False):
        self.info_label.set_text(text)
        self.info_button.set_visible(button)
        self.infobar.set_message_type(Gtk.MessageType.WARNING if button else Gtk.MessageType.INFO)
        self.infobar.show()

    def refresh(self):
        if self.google_view is not None and self.settings.get("google_messages"):
            self.google_view.reload()
            return
        if self.device:
            self.client.request_conversations(self.device)
            if self.thread_id is not None:
                self.client.request_thread(self.device, self.thread_id, 0, PAGE)

    def _load_contacts(self):
        folder = self.client.contacts_folder()
        self.directory = Directory(folder)
        self.directory.load(self.device)
        self.people = People(self.directory)
        self.recipients.set_contacts(self.directory.contacts)
        if self.device:
            self._refresh_list()
            if self.thread_id is not None:
                self._show_thread(keep_position=True)

    # -- conversations -------------------------------------------------------------

    def _refresh_list(self):
        self.list.set_conversations(self.store.conversations(), self.people, self.thread_id)

    def open_thread(self, thread_id):
        self.thread_id = thread_id
        self.pending_new = None
        self._show_thread()
        self.stack.set_visible_child_name("thread")
        self.list.select(thread_id)
        self._show_detail()
        self.client.request_thread(self.device, thread_id, 0, PAGE)
        self.thread_view.composer.text.grab_focus()

    def _show_thread(self, keep_position=False):
        messages = self.store.messages(self.thread_id) if self.thread_id is not None else []
        pending = [m for m in self.pending if m["thread_id"] == self.thread_id]
        addresses = (messages or pending or [{"addresses": []}])[-1]["addresses"]
        title = self.people.title(addresses)
        contact = self.people.contact(addresses[0]) if len(addresses) == 1 else None
        subtitle = ", ".join(addresses)
        self.thread_view.set_header(title, subtitle, contact.photo if contact else None, len(addresses) > 1)
        self.thread_view.show_messages(messages + pending, self.people, keep_position)

    def _load_older(self):
        if self.device and self.thread_id is not None:
            oldest = self.store.oldest_date(self.thread_id)
            if oldest:
                self.client.request_thread(self.device, self.thread_id, oldest, PAGE)

    def _on_messages(self, device, messages):
        if device != self.device:
            return
        oldest = self.store.oldest_date(self.thread_id) if self.thread_id is not None else 0
        changed = self.store.add(messages)
        for message in messages:
            if message["direction"] == "sent":
                self.pending = [p for p in self.pending
                                if not (p["body"] == message["body"]
                                        and (p["thread_id"] in (None, message["thread_id"])))]
                if self.pending_new and address_set(message["addresses"]) == self.pending_new:
                    self.pending_new = None
                    GLib.idle_add(lambda t=message["thread_id"]: self.open_thread(t) and False)
        self._refresh_list()
        if self.thread_id in changed:
            older = all(m["date"] < oldest for m in messages if m["thread_id"] == self.thread_id) and oldest
            self._show_thread(keep_position=bool(older))

    # -- sending -------------------------------------------------------------------

    def _pending(self, addresses, text, thread_id):
        message = {"id": -int(time.time() * 1000), "thread_id": thread_id, "date": int(time.time() * 1000),
                   "type": 4, "direction": "pending", "body": text, "addresses": list(addresses),
                   "read": True, "attachments": []}
        self.pending.append(message)
        return message

    def _send(self, text, files):
        latest = self.store.latest(self.thread_id) if self.thread_id is not None else None
        addresses = latest["addresses"] if latest else []
        if not addresses or not self.device:
            return
        message = self._pending(addresses, text, self.thread_id)
        if not self.client.send_message(self.device, addresses, text, files):
            message["direction"] = "failed"
        self.thread_view.composer.clear()
        self._show_thread()

    def _send_new(self, text, files):
        self.recipients.add_typed()
        addresses = list(self.recipients.recipients)
        if not addresses or not self.device:
            return
        existing = self.store.thread_for(addresses)
        if existing is not None:
            self.open_thread(existing)
            self.new_composer.clear()
            self._send(text, files)
            return
        message = self._pending(addresses, text, None)
        if not self.client.send_message(self.device, addresses, text, files):
            message["direction"] = "failed"
        self.new_composer.clear()
        self.pending_new = address_set(addresses)
        self.thread_id = None
        self._show_thread()
        self.stack.set_visible_child_name("thread")

    def new_message(self, numbers=()):
        numbers = list(numbers)
        existing = self.store.thread_for(numbers) if numbers else None
        if existing is not None:
            self.open_thread(existing)
            return
        self.thread_id = None
        self.list.clear_selection()
        self.recipients.reset(numbers)
        self.stack.set_visible_child_name("new")
        self._show_detail()
        (self.new_composer.text if numbers else self.recipients.entry).grab_focus()

    # -- attachments ---------------------------------------------------------------

    def _open_attachment(self, attachment):
        uid = attachment["unique_identifier"]
        self._wanted_attachments.add(uid)
        self.client.request_attachment(self.device, attachment["part_id"], uid)

    def _on_attachment(self, uid, path):
        if uid in self._wanted_attachments:
            self._wanted_attachments.discard(uid)
            try:
                Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(path), None)
            except GLib.Error as error:
                print("Could not open {}: {}".format(path, error.message), flush=True)

    # -- Google Messages for web -------------------------------------------------------

    def _apply_mode(self):
        """Show Google's page or Satellite's view. Returns True for Google's."""
        google = self.settings.get("google_messages") and google_messages.available()
        self.sign_out_action.set_enabled(google)
        if google:
            if self.google_view is None:
                self.google_view = google_messages.GoogleMessagesView()
                self.modes.add_named(self.google_view, "google")
            self.modes.set_visible_child_name("google")
            self.header.set_subtitle(_("Google Messages for web"))
            self.refresh_button.set_tooltip_text(_("Reload Google Messages (F5)"))
        else:
            self.modes.set_visible_child_name("satellite")
            device = self._device()
            self.header.set_subtitle(device["name"] if device else None)
            self.refresh_button.set_tooltip_text(_("Load messages from the phone (F5)"))
        self.new_button.set_visible(not google)
        self.back_button.set_visible(not google and bool(self._narrow and self._showing_detail))
        return google

    def _on_google_toggled(self, action, value):
        turning_on = value.get_boolean()
        if turning_on:
            if not google_messages.available():
                self._message(_("Google Messages for web needs WebKitGTK"),
                              _("Install the gir1.2-webkit2-4.1 package, then try again."))
                return
            if not self._ask(_("Use Google Messages for web?"), GOOGLE_WARNING, _("Use Google Messages")):
                return
            remove_data = False
        else:
            choice = self._ask(
                _("Stop using Google Messages for web?"),
                _("Satellite will show your phone's SMS and MMS again."),
                _("Stop Using"), check=_("Also sign out and delete Google Messages data from this computer"))
            if not choice:
                return
            remove_data = choice == "checked"
        self.settings.set("google_messages", turning_on)
        action.set_state(value)
        if not turning_on:
            self._close_google(remove_data)
        self._apply_mode()
        if not turning_on:
            self.start(*(self._requested or (None, None)))

    def _close_google(self, remove_data):
        view, self.google_view = self.google_view, None

        def forget():
            google_messages.delete_data()
            if view is not None:
                view.destroy()

        if view is None:
            if remove_data:
                google_messages.delete_data()
        elif remove_data:
            view.sign_out(forget)
        else:
            view.destroy()

    def _sign_out(self):
        if self.google_view is None:
            return
        if self._ask(_("Sign out of Google Messages?"),
                     _("This computer forgets the pairing. To use it again, scan a new QR code with the phone."),
                     _("Sign Out")):
            self.google_view.sign_out(self.google_view.reload)

    def _ask(self, title, text, accept, check=None):
        """A confirmation dialog. With ``check``, returns "checked"/"unchecked"/None."""
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.NONE, text=title)
        dialog.format_secondary_text(text)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(accept, Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        box = None
        if check:
            box = Gtk.CheckButton(label=check, active=True)
            box.set_margin_start(12)
            dialog.get_message_area().pack_start(box, False, False, 0)
            box.show()
        accepted = dialog.run() == Gtk.ResponseType.ACCEPT
        checked = box.get_active() if box else False
        dialog.destroy()
        if not accepted:
            return None if check else False
        return ("checked" if checked else "unchecked") if check else True

    def _message(self, title, text):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.OK, text=title)
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()

    # -- narrow windows (like Satellite's main window) -------------------------------

    def _show_detail(self):
        self._showing_detail = True
        self._apply_layout()

    def _go_back(self):
        self._showing_detail = False
        self.list.clear_selection()
        self._apply_layout()

    def _on_size_allocate(self, _widget, allocation):
        narrow = allocation.width < NARROW_WIDTH
        if narrow != self._narrow:
            self._narrow = narrow
            GLib.idle_add(self._apply_layout)

    def _apply_layout(self):
        if self.google_view is not None and self.settings.get("google_messages"):
            self.back_button.hide()  # Google's page has its own navigation
            return False
        if not self._narrow:
            self.list.show()
            self.divider.show()
            self.stack.show()
            self.back_button.hide()
            return False
        self.divider.hide()
        self.list.set_visible(not self._showing_detail)
        self.stack.set_visible(self._showing_detail)
        self.back_button.set_visible(self._showing_detail)
        self.layout.set_child_packing(self.list, not self._showing_detail, True, 0, Gtk.PackType.START)
        return False
