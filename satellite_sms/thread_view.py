"""One conversation: chat bubbles, pictures and the reply box."""

import base64
import os

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Gtk, Pango

from satellite.ui.avatar import Avatar
from satellite_sms.model import bubble_time, day_label
from satellite.i18n import _

CSS = b"""
.sms-bubble { border-radius: 16px; padding: 8px 12px; }
.sms-bubble.received { background-color: alpha(@theme_fg_color, 0.08); }
.sms-bubble.sent { background-color: @theme_selected_bg_color; color: @theme_selected_fg_color; }
.sms-bubble.failed { background-color: alpha(@error_color, 0.25); }
.sms-bubble.pending { opacity: 0.6; }
.sms-day { font-size: smaller; }
.satellite-plain-list, .satellite-plain-list row:not(:selected):not(:hover) { background-color: transparent; }
.sms-composer { border-top: 1px solid alpha(@theme_fg_color, 0.1); }
"""
_css_installed = False


def install_css():
    global _css_installed
    screen = Gdk.Screen.get_default()
    if _css_installed or screen is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_installed = True


def thumbnail(encoded, size=220):
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(base64.b64decode(encoded))
        loader.close()
        pixbuf = loader.get_pixbuf()
    except (GLib.Error, ValueError, TypeError):
        return None
    if pixbuf is None:
        return None
    scale = min(1.0, size / max(pixbuf.get_width(), pixbuf.get_height()))
    return pixbuf.scale_simple(max(1, int(pixbuf.get_width() * scale)),
                               max(1, int(pixbuf.get_height() * scale)), GdkPixbuf.InterpType.BILINEAR)


class Bubble(Gtk.ListBoxRow):
    def __init__(self, message, sender_name=None, on_attachment=None):
        super().__init__(activatable=False, selectable=False)
        self.message = message
        sent = message["direction"] in ("sent", "sending", "failed", "pending")
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        outer.set_halign(Gtk.Align.END if sent else Gtk.Align.START)
        outer.set_margin_start(60 if sent else 12)
        outer.set_margin_end(12 if sent else 60)
        outer.set_margin_top(2)
        outer.set_margin_bottom(2)
        if sender_name:
            label = Gtk.Label(label=sender_name, xalign=0)
            label.get_style_context().add_class("dim-label")
            outer.pack_start(label, False, False, 0)

        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        style = bubble.get_style_context()
        style.add_class("sms-bubble")
        style.add_class("sent" if sent else "received")
        if message["direction"] == "failed":
            style.add_class("failed")
        if message["direction"] == "pending":
            style.add_class("pending")
        for attachment in message.get("attachments", ()):
            bubble.pack_start(self._attachment(attachment, on_attachment), False, False, 0)
        if message["body"]:
            text = Gtk.Label(label=message["body"], xalign=0, wrap=True, selectable=True)
            text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            text.set_max_width_chars(48)
            bubble.pack_start(text, False, False, 0)
        outer.pack_start(bubble, False, False, 0)

        status = {"failed": "Not sent", "pending": "Sending…", "sending": "Sending…"}.get(
            message["direction"], bubble_time(message["date"]))
        meta = Gtk.Label(label=status, xalign=1 if sent else 0)
        meta.get_style_context().add_class("dim-label")
        meta.get_style_context().add_class("sms-day")
        outer.pack_start(meta, False, False, 0)
        self.add(outer)

    @staticmethod
    def _attachment(attachment, on_attachment):
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text(_("Open"))
        pixbuf = thumbnail(attachment["thumbnail"]) if attachment.get("thumbnail") else None
        if pixbuf is not None:
            button.add(Gtk.Image.new_from_pixbuf(pixbuf))
        else:
            box = Gtk.Box(spacing=6)
            kind = attachment.get("mime_type", "").split("/")[0]
            icon = {"image": "image-x-generic-symbolic", "video": "video-x-generic-symbolic",
                    "audio": "audio-x-generic-symbolic"}.get(kind, "text-x-generic-symbolic")
            box.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR), False, False, 0)
            box.pack_start(Gtk.Label(label=attachment.get("unique_identifier") or _("Attachment")), False, False, 0)
            button.add(box)
        if on_attachment:
            button.connect("clicked", lambda _b: on_attachment(attachment))
        return button


class DaySeparator(Gtk.ListBoxRow):
    def __init__(self, text):
        super().__init__(activatable=False, selectable=False)
        label = Gtk.Label(label=text)
        label.get_style_context().add_class("dim-label")
        label.get_style_context().add_class("sms-day")
        label.set_margin_top(12)
        label.set_margin_bottom(6)
        self.add(label)


class Composer(Gtk.Box):
    """Text box, attachments and the send button. Enter sends, Shift+Enter adds a line."""

    __gsignals__ = {"send": (GObject.SignalFlags.RUN_FIRST, None, (str, object))}

    def __init__(self, parent_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._parent_window = parent_window
        self.files = []
        self.get_style_context().add_class("sms-composer")
        self.set_border_width(8)
        self.chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        self.pack_start(self.chips, False, False, 0)

        row = Gtk.Box(spacing=6)
        attach = Gtk.Button.new_from_icon_name("mail-attachment-symbolic", Gtk.IconSize.BUTTON)
        attach.set_tooltip_text(_("Attach a picture or file"))
        attach.set_valign(Gtk.Align.END)
        attach.connect("clicked", self._on_attach)
        row.pack_start(attach, False, False, 0)

        frame = Gtk.Frame()
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(140)
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        for side in ("top", "bottom", "left", "right"):
            getattr(self.text, "set_{}_margin".format(side))(6)
        self.text.connect("key-press-event", self._on_key)
        self.text.get_buffer().connect("changed", lambda _b: self._update_button())
        scroller.add(self.text)
        frame.add(scroller)
        row.pack_start(frame, True, True, 0)

        self.send_button = Gtk.Button.new_from_icon_name("document-send-symbolic", Gtk.IconSize.BUTTON)
        self.send_button.set_tooltip_text(_("Send (Enter)"))
        self.send_button.set_valign(Gtk.Align.END)
        self.send_button.get_style_context().add_class("suggested-action")
        self.send_button.connect("clicked", lambda _b: self._send())
        row.pack_start(self.send_button, False, False, 0)
        self.pack_start(row, False, False, 0)
        self._update_button()

    def _on_key(self, _view, event):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not event.state & Gdk.ModifierType.SHIFT_MASK:
            self._send()
            return True
        return False

    def _text(self):
        buffer = self.text.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).strip()

    def _update_button(self):
        self.send_button.set_sensitive(bool(self._text() or self.files))

    def _send(self):
        text = self._text()
        if text or self.files:
            self.emit("send", text, list(self.files))

    def clear(self):
        self.text.get_buffer().set_text("")
        self.files = []
        self._refresh_chips()

    def _on_attach(self, _button):
        chooser = Gtk.FileChooserNative.new(_("Attach"), self._parent_window, Gtk.FileChooserAction.OPEN, _("Attach"), None)
        chooser.set_select_multiple(True)
        if chooser.run() == Gtk.ResponseType.ACCEPT:
            self.files += [f for f in chooser.get_filenames() if f not in self.files]
            self._refresh_chips()
        chooser.destroy()

    def _refresh_chips(self):
        for child in self.chips.get_children():
            self.chips.remove(child)
        for path in self.files:
            chip = Gtk.Box(spacing=4)
            chip.pack_start(Gtk.Image.new_from_icon_name("mail-attachment-symbolic", Gtk.IconSize.MENU), False, False, 0)
            chip.pack_start(Gtk.Label(label=os.path.basename(path)), False, False, 0)
            remove = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
            remove.set_relief(Gtk.ReliefStyle.NONE)
            remove.connect("clicked", lambda _b, p=path: (self.files.remove(p), self._refresh_chips()))
            chip.pack_start(remove, False, False, 0)
            self.chips.add(chip)
        self.chips.show_all()
        self._update_button()


class ThreadView(Gtk.Box):
    """Header, scrolling bubbles and the composer for one conversation."""

    __gsignals__ = {
        "send": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        "load-older": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "open-attachment": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, parent_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        install_css()
        self.header = Gtk.Box(spacing=10)
        self.header.set_border_width(10)
        self.pack_start(self.header, False, False, 0)
        self.pack_start(Gtk.Separator(), False, False, 0)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.connect("edge-reached", self._on_edge)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.get_style_context().add_class("satellite-plain-list")
        self.listbox.set_valign(Gtk.Align.END)
        self.scroller.add(self.listbox)
        self.pack_start(self.scroller, True, True, 0)

        self.composer = Composer(parent_window)
        self.composer.connect("send", lambda _c, text, files: self.emit("send", text, files))
        self.pack_start(self.composer, False, False, 0)
        self._stick_to_bottom = True
        self._older_requested = False

    def set_header(self, title, subtitle, photo=None, group=False):
        for child in self.header.get_children():
            self.header.remove(child)
        self.header.pack_start(Avatar(title, photo, 36, "system-users-symbolic" if group else "avatar-default-symbolic"),
                               False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        name.set_markup("<b>{}</b>".format(GLib.markup_escape_text(title)))
        texts.pack_start(name, False, False, 0)
        if subtitle and subtitle != title:
            sub = Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            sub.get_style_context().add_class("dim-label")
            texts.pack_start(sub, False, False, 0)
        self.header.pack_start(texts, True, True, 0)
        self.header.show_all()

    def show_messages(self, messages, people, keep_position=False):
        adjustment = self.scroller.get_vadjustment()
        from_bottom = adjustment.get_upper() - adjustment.get_value()
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        last_day = None
        group = len({a for m in messages for a in m["addresses"]}) > 1
        for message in messages:
            day = day_label(message["date"])
            if day != last_day:
                self.listbox.add(DaySeparator(day))
                last_day = day
            sender = None
            if group and message["direction"] == "received" and message["addresses"]:
                sender = people.name(message["addresses"][0])
            self.listbox.add(Bubble(message, sender, lambda a: self.emit("open-attachment", a)))
        self.listbox.show_all()
        self._older_requested = False
        GLib.idle_add(self._restore, from_bottom if keep_position else 0)

    def _restore(self, from_bottom):
        adjustment = self.scroller.get_vadjustment()
        if from_bottom:  # older messages were added above: keep what was on screen
            adjustment.set_value(adjustment.get_upper() - from_bottom)
        else:
            adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        return False

    def _on_edge(self, _scroller, position):
        if position == Gtk.PositionType.TOP and not self._older_requested:
            self._older_requested = True
            self.emit("load-older")


class RecipientBar(Gtk.Box):
    """'To:' with completion from contacts; Enter or comma adds a recipient."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__(spacing=8)
        self.set_border_width(10)
        self.recipients = []
        self.namer = lambda number: number  # the window shows contact names
        self.pack_start(Gtk.Label(label=_("To:")), False, False, 0)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=4, row_spacing=4)
        column.pack_start(self.chips, False, False, 0)
        self.entry = Gtk.Entry(placeholder_text=_("Name or phone number"))
        self.entry.connect("activate", lambda _e: self.add_typed())
        self.entry.connect("changed", self._on_changed)
        column.pack_start(self.entry, False, False, 0)
        self.pack_start(column, True, True, 0)
        self.store = Gtk.ListStore(str, str, str)  # display, number, search text
        completion = Gtk.EntryCompletion(model=self.store, text_column=0, minimum_key_length=1)
        completion.set_match_func(lambda _c, key, it: key.lower() in self.store[it][2])
        completion.connect("match-selected", self._on_match)
        self.entry.set_completion(completion)

    def set_contacts(self, contacts):
        self.store.clear()
        for contact in contacts:
            for label, number in contact.phones:
                display = "{} ({}: {})".format(contact.display_name, _(label) if label else "", number)
                self.store.append([display, number, "{} {}".format(contact.display_name, number).lower()])

    def _on_match(self, _completion, model, it):
        self.add(model[it][1])
        return True

    def _on_changed(self, entry):
        if entry.get_text().endswith(","):
            self.add_typed()

    def add_typed(self):
        text = self.entry.get_text().strip().rstrip(",").strip()
        if text:
            self.add(text)

    def add(self, number):
        self.entry.set_text("")
        if number not in self.recipients:
            self.recipients.append(number)
            self._refresh()
            self.emit("changed")

    def reset(self, numbers=()):
        self.recipients = list(numbers)
        self.entry.set_text("")
        self._refresh()

    def _refresh(self):
        for child in self.chips.get_children():
            self.chips.remove(child)
        for number in self.recipients:
            chip = Gtk.Box(spacing=2)
            chip.pack_start(Gtk.Label(label=self.namer(number)), False, False, 0)
            remove = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
            remove.set_relief(Gtk.ReliefStyle.NONE)
            remove.connect("clicked", lambda _b, n=number: (self.recipients.remove(n), self._refresh(),
                                                           self.emit("changed")))
            chip.pack_start(remove, False, False, 0)
            self.chips.add(chip)
        if self.recipients:
            self.chips.show_all()
        else:
            self.chips.hide()
