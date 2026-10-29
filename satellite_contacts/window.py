"""The Satellite Contacts window: a read-only copy of the phone's address book."""

import os

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from satellite.launch import launch
from satellite.ui.avatar import Avatar
from satellite.vcard import Directory
from satellite.i18n import _, ngettext

NARROW_WIDTH = 640
CSS = b"""
.satellite-plain-list, .satellite-plain-list row:not(:selected):not(:hover) { background-color: transparent; }
.contacts-letter { font-weight: bold; }
"""


def install_css():
    screen = Gdk.Screen.get_default()
    if screen is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def letter(contact):
    first = contact.display_name[:1].upper()
    return first if first.isalpha() else "#"


class ContactRow(Gtk.ListBoxRow):
    def __init__(self, contact):
        super().__init__()
        self.contact = contact
        self.search_text = " ".join(
            [contact.display_name, contact.organization] + [n for _l, n in contact.phones]
            + [e for _l, e in contact.emails]).lower()
        box = Gtk.Box(spacing=10)
        box.set_border_width(6)
        box.set_margin_start(4)
        box.pack_start(Avatar(contact.display_name, contact.photo, 32), False, False, 0)
        box.pack_start(Gtk.Label(label=contact.display_name, xalign=0, ellipsize=Pango.EllipsizeMode.END),
                       True, True, 0)
        self.add(box)


class Details(Gtk.ScrolledWindow):
    """Everything known about one contact, with actions for each number."""

    def __init__(self, window):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._window = window
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.box.set_border_width(24)
        self.box.set_halign(Gtk.Align.CENTER)
        self.box.set_size_request(340, -1)
        self.add(self.box)

    def show_contact(self, contact, device_name):
        for child in self.box.get_children():
            self.box.remove(child)
        self.box.pack_start(Avatar(contact.display_name, contact.photo, 96), False, False, 6)
        name = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, selectable=True)
        name.set_markup("<span size='x-large'>{}</span>".format(GLib.markup_escape_text(contact.display_name)))
        self.box.pack_start(name, False, False, 0)
        if contact.organization:
            org = Gtk.Label(label=contact.organization, wrap=True, selectable=True)
            org.get_style_context().add_class("dim-label")
            self.box.pack_start(org, False, False, 0)

        rows = []
        for label, number in contact.phones:
            rows.append((label, number, [
                ("chat-message-new-symbolic", _("Send a text message"),
                 lambda n=number: self._window.message(n)),
                ("edit-copy-symbolic", _("Copy"), lambda n=number: self._window.copy(n)),
            ]))
        for label, address in contact.emails:
            rows.append((label, address, [
                ("mail-message-new-symbolic", _("Write an email"),
                 lambda a=address: self._window.open_uri("mailto:" + a)),
                ("edit-copy-symbolic", _("Copy"), lambda a=address: self._window.copy(a)),
            ]))
        for label, address in contact.addresses:
            rows.append((_("Address") if label == "Phone" else label, address, [
                ("edit-copy-symbolic", _("Copy"), lambda a=address: self._window.copy(a))]))
        if contact.birthday:
            rows.append((_("Birthday"), contact.birthday, []))
        if contact.note:
            rows.append((_("Note"), contact.note, []))
        if rows:
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            listbox.get_style_context().add_class("frame")
            listbox.set_margin_top(18)
            for label, value, actions in rows:
                listbox.add(self._row(label, value, actions))
            self.box.pack_start(listbox, False, False, 0)

        footer = Gtk.Label(
            label=_("Copied from {}. Change this contact on the phone.").format(device_name or _("the phone")),
            wrap=True, justify=Gtk.Justification.CENTER)
        footer.get_style_context().add_class("dim-label")
        footer.set_margin_top(18)
        self.box.pack_start(footer, False, False, 0)
        self.box.show_all()

    @staticmethod
    def _row(label, value, actions):
        row = Gtk.ListBoxRow(activatable=False)
        box = Gtk.Box(spacing=8)
        box.set_border_width(10)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        caption = Gtk.Label(label=_(label) if label else "", xalign=0)
        caption.get_style_context().add_class("dim-label")
        texts.pack_start(caption, False, False, 0)
        texts.pack_start(Gtk.Label(label=value, xalign=0, wrap=True, selectable=True), False, False, 0)
        box.pack_start(texts, True, True, 0)
        for icon, tooltip, callback in actions:
            button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            button.set_tooltip_text(tooltip)
            button.set_valign(Gtk.Align.CENTER)
            button.connect("clicked", lambda _b, cb=callback: cb())
            box.pack_start(button, False, False, 0)
        row.add(box)
        return row


class ContactsWindow(Gtk.ApplicationWindow):
    def __init__(self, app, client):
        super().__init__(application=app, title=_("Contacts"))
        install_css()
        self.set_default_size(820, 580)
        self.client = client
        self.devices = []
        self.device = None
        self.directory = None
        self._narrow = None
        self._showing_detail = False
        self._requested = None
        self._build()
        self.connect("size-allocate", self._on_size_allocate)
        client.connect("available", lambda _c, up: self._load_devices())
        client.connect("devices-changed", lambda _c: self._load_devices())
        client.connect("contacts-changed", self._on_contacts_changed)

    def _build(self):
        header = Gtk.HeaderBar(show_close_button=True, title=_("Contacts"))
        self.header = header
        self.set_titlebar(header)
        self.back_button = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        self.back_button.set_tooltip_text(_("Back to contacts"))
        self.back_button.connect("clicked", lambda _b: self._go_back())
        header.pack_start(self.back_button)
        self.sync_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self.sync_button.set_tooltip_text(_("Copy contacts from the phone again"))
        self.sync_button.connect("clicked", lambda _b: self.sync())
        header.pack_end(self.sync_button)

        self.layout = Gtk.Box()
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side.set_size_request(280, -1)
        self.device_combo = Gtk.ComboBoxText(margin_start=8, margin_end=8, margin_top=8)
        self.device_combo.connect("changed", self._on_device_chosen)
        side.pack_start(self.device_combo, False, False, 0)
        self.search = Gtk.SearchEntry(placeholder_text=_("Search contacts"), margin=8)
        self.search.connect("search-changed", lambda _e: self.listbox.invalidate_filter())
        side.pack_start(self.search, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.listbox = Gtk.ListBox()
        self.listbox.get_style_context().add_class("satellite-plain-list")
        self.listbox.set_filter_func(self._filter)
        self.listbox.set_header_func(self._header)
        self.listbox.connect("row-selected", self._on_selected)
        scroller.add(self.listbox)
        side.pack_start(scroller, True, True, 0)
        self.side = side
        self.layout.pack_start(side, False, True, 0)
        self.divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.layout.pack_start(self.divider, False, False, 0)

        self.stack = Gtk.Stack(hhomogeneous=False)
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER)
        empty.pack_start(Gtk.Image.new_from_icon_name("x-office-address-book-symbolic", Gtk.IconSize.DIALOG),
                         False, False, 0)
        self.empty_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=40)
        self.empty_label.get_style_context().add_class("dim-label")
        empty.pack_start(self.empty_label, False, False, 0)
        self.empty_button = Gtk.Button(label=_("Copy contacts from the phone"), halign=Gtk.Align.CENTER)
        self.empty_button.get_style_context().add_class("suggested-action")
        self.empty_button.connect("clicked", lambda _b: self.sync())
        empty.pack_start(self.empty_button, False, False, 0)
        self.stack.add_named(empty, "empty")
        self.details = Details(self)
        self.stack.add_named(self.details, "details")
        self.layout.pack_start(self.stack, True, True, 0)
        self.add(self.layout)
        self.layout.show_all()
        header.show_all()
        self.back_button.hide()

    # -- data ------------------------------------------------------------------

    def start(self, device=None):
        self._requested = device
        if self.client.available:
            self._load_devices()
        elif not self.client.start_satellite():
            self._load_devices()  # contacts already on disk still show

    def _load_devices(self):
        devices = [d for d in (self.client.devices() if self.client.available else []) if d.get("contacts")]
        folder = self.client.contacts_folder() if self.client.available else None
        if folder is None:
            from satellite.plugins.contacts import contacts_root
            folder = contacts_root()
        self.folder = folder
        try:  # phones synced earlier but not known to Satellite right now
            known = {d["id"] for d in devices}
            devices += [{"id": d, "name": _("Phone"), "connected": False}
                        for d in sorted(os.listdir(folder)) if d not in known]
        except OSError:
            pass
        self.devices = devices
        current = self._requested if any(d["id"] == self._requested for d in devices) else self.device
        if not any(d["id"] == current for d in devices):
            current = devices[0]["id"] if devices else None
        self.device_combo.handler_block_by_func(self._on_device_chosen)
        self.device_combo.remove_all()
        for device in devices:
            self.device_combo.append(device["id"], device["name"])
        if current:
            self.device_combo.set_active_id(current)
        self.device_combo.handler_unblock_by_func(self._on_device_chosen)
        self.device_combo.set_visible(len(devices) > 1)
        if current != self.device or self.directory is None:
            self.device = current
            self._reload()

    def _on_contacts_changed(self, _client, device):
        if device == self.device:
            self._reload()

    def _on_device_chosen(self, combo):
        if combo.get_active_id() and combo.get_active_id() != self.device:
            self.device = combo.get_active_id()
            self._reload()

    def _device_name(self):
        return next((d["name"] for d in self.devices if d["id"] == self.device), None)

    def _reload(self):
        self.header.set_subtitle(self._device_name())
        selected = self.listbox.get_selected_row()
        selected_uid = selected.contact.uid if selected else None
        self.directory = Directory(self.folder)
        contacts = self.directory.load(self.device) if self.device else []
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        for contact in contacts:
            row = ContactRow(contact)
            self.listbox.add(row)
            if contact.uid == selected_uid:
                self.listbox.select_row(row)
        self.listbox.show_all()
        if not contacts:
            name = self._device_name()
            self.empty_label.set_text(
                _("No contacts yet. Connect {} and copy its contacts.").format(name) if name
                else _("Pair a phone in Satellite to see its contacts here."))
            self.empty_button.set_visible(bool(self.device) and self.client.available)
            self.stack.set_visible_child_name("empty")
        elif selected_uid is None:
            self.empty_label.set_text(ngettext("{} contact", "{} contacts", len(contacts)).format(len(contacts)))
            self.empty_button.hide()
            self.stack.set_visible_child_name("empty")

    def sync(self):
        if self.device and self.client.available and not self.client.sync_contacts(self.device):
            self.empty_label.set_text(_("The phone is not connected."))

    # -- list ------------------------------------------------------------------

    def _filter(self, row):
        query = self.search.get_text().strip().lower()
        return not query or query in row.search_text

    @staticmethod
    def _header(row, before):
        if before is None or letter(before.contact) != letter(row.contact):
            label = Gtk.Label(label=letter(row.contact), xalign=0, margin_start=12, margin_top=8)
            label.get_style_context().add_class("contacts-letter")
            label.get_style_context().add_class("dim-label")
            row.set_header(label)
        else:
            row.set_header(None)

    def _on_selected(self, _listbox, row):
        if row is None:
            return
        self.details.show_contact(row.contact, self._device_name())
        self.stack.set_visible_child_name("details")
        self._showing_detail = True
        self._apply_layout()

    def select_name(self, name):
        for row in self.listbox.get_children():
            if row.contact.display_name == name:
                self.listbox.select_row(row)
                return True
        return False

    # -- actions ---------------------------------------------------------------

    def message(self, number):
        launch("satellite-sms", "satellite_sms", "--device", self.device or "", "--to", number)


    def copy(self, text):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)

    def open_uri(self, uri):
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as error:
            print("Could not open {}: {}".format(uri, error.message), flush=True)

    # -- narrow windows ----------------------------------------------------------

    def _go_back(self):
        self._showing_detail = False
        self.listbox.unselect_all()
        self._apply_layout()

    def _on_size_allocate(self, _widget, allocation):
        narrow = allocation.width < NARROW_WIDTH
        if narrow != self._narrow:
            self._narrow = narrow
            GLib.idle_add(self._apply_layout)

    def _apply_layout(self):
        if not self._narrow:
            self.side.show()
            self.divider.show()
            self.stack.show()
            self.back_button.hide()
            return False
        self.divider.hide()
        self.side.set_visible(not self._showing_detail)
        self.stack.set_visible(self._showing_detail)
        self.back_button.set_visible(self._showing_detail)
        self.layout.set_child_packing(self.side, not self._showing_detail, True, 0, Gtk.PackType.START)
        return False
