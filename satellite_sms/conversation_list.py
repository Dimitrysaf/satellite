"""The left-hand list of conversations."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, GObject, Gtk, Pango

from satellite.ui.avatar import Avatar
from satellite_sms.model import list_time
from satellite.i18n import _


def preview(message):
    text = message["body"].replace("\n", " ") or (_("Picture") if message["attachments"] else "")
    return _("You: {}").format(text) if message["direction"] in ("sent", "sending", "failed") else text


class ConversationRow(Gtk.ListBoxRow):
    def __init__(self, message, people):
        super().__init__()
        self.thread_id = message["thread_id"]
        self.addresses = message["addresses"]
        title = people.title(message["addresses"])
        contact = people.contact(message["addresses"][0]) if len(message["addresses"]) == 1 else None
        self.search_text = " ".join([title, message["body"], *message["addresses"]]).lower()
        unread = not message["read"] and message["direction"] == "received"

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_border_width(8)
        box.pack_start(Avatar(title if not contact else contact.display_name,
                              contact.photo if contact else None, 40,
                              "system-users-symbolic" if len(message["addresses"]) > 1 else "avatar-default-symbolic"),
                       False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        name.set_markup("<b>{}</b>".format(GLib.markup_escape_text(title)) if unread
                        else GLib.markup_escape_text(title))
        top.pack_start(name, True, True, 0)
        when = Gtk.Label(label=list_time(message["date"]))
        when.get_style_context().add_class("dim-label")
        top.pack_end(when, False, False, 0)
        texts.pack_start(top, False, False, 0)
        body = Gtk.Label(label=preview(message), xalign=0, ellipsize=Pango.EllipsizeMode.END)
        if not unread:
            body.get_style_context().add_class("dim-label")
        texts.pack_start(body, False, False, 0)
        box.pack_start(texts, True, True, 0)
        self.add(box)


class ConversationList(Gtk.Box):
    __gsignals__ = {"selected": (GObject.SignalFlags.RUN_FIRST, None, (object,))}

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.search = Gtk.SearchEntry(placeholder_text=_("Search conversations"))
        self.search.set_margin_start(8)
        self.search.set_margin_end(8)
        self.search.set_margin_top(8)
        self.search.set_margin_bottom(8)
        self.search.connect("search-changed", lambda _e: self.listbox.invalidate_filter())
        self.pack_start(self.search, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.listbox = Gtk.ListBox()
        self.listbox.get_style_context().add_class("satellite-plain-list")
        self.listbox.set_filter_func(self._filter)
        self.listbox.connect("row-selected", self._on_selected)
        scroller.add(self.listbox)
        self.pack_start(scroller, True, True, 0)
        self._updating = False

    def _filter(self, row):
        query = self.search.get_text().strip().lower()
        return not query or query in row.search_text

    def _on_selected(self, _listbox, row):
        if row is not None and not self._updating:
            self.emit("selected", row)

    def set_conversations(self, conversations, people, selected=None):
        self._updating = True
        try:
            for child in self.listbox.get_children():
                self.listbox.remove(child)
            for message in conversations:
                row = ConversationRow(message, people)
                self.listbox.add(row)
                if message["thread_id"] == selected:
                    self.listbox.select_row(row)
            self.listbox.show_all()
        finally:
            self._updating = False

    def select(self, thread_id):
        for row in self.listbox.get_children():
            if row.thread_id == thread_id:
                self.listbox.select_row(row)
                return True
        self.listbox.unselect_all()
        return False

    def clear_selection(self):
        self._updating = True
        self.listbox.unselect_all()
        self._updating = False
