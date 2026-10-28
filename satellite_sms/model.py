"""Conversations and messages, without any GTK."""

import datetime

from satellite.vcard import number_key
from satellite.i18n import _, ngettext


def _local(ms):
    return datetime.datetime.fromtimestamp(ms / 1000)


def list_time(ms, now=None):
    """Short time for the conversation list: 14:05, Yesterday, Mon, 12 Oct, 12/10/2023."""
    now = now or datetime.datetime.now()
    when = _local(ms)
    days = (now.date() - when.date()).days
    if days <= 0:
        return when.strftime("%H:%M")
    if days == 1:
        return _("Yesterday")
    if days < 7:
        return when.strftime("%a")
    if when.year == now.year:
        return "{} {}".format(when.day, when.strftime("%b"))
    return when.strftime("%d/%m/%Y")


def day_label(ms, now=None):
    """Separator between days inside a conversation."""
    now = now or datetime.datetime.now()
    when = _local(ms)
    days = (now.date() - when.date()).days
    if days <= 0:
        return _("Today")
    if days == 1:
        return _("Yesterday")
    if days < 7:
        return when.strftime("%A")
    if when.year == now.year:
        return "{} {} {}".format(when.strftime("%A"), when.day, when.strftime("%B"))
    return "{} {} {}".format(when.day, when.strftime("%B"), when.year)


def bubble_time(ms):
    return _local(ms).strftime("%H:%M")


def address_set(addresses):
    return frozenset(number_key(a) for a in addresses)


class People:
    """Names for phone numbers, from the synced contacts."""

    def __init__(self, directory=None):
        self.directory = directory

    def contact(self, address):
        return self.directory.find_number(address) if self.directory else None

    def name(self, address):
        contact = self.contact(address)
        return contact.display_name if contact else address

    def title(self, addresses):
        names = [self.name(a) for a in addresses]
        if len(names) <= 3:
            return ", ".join(names) or _("Unknown")
        others = len(names) - 2
        return ngettext("{}, {} and {} other", "{}, {} and {} others", others).format(names[0], names[1], others)


class Store:
    """Every message seen for one device, grouped in threads."""

    def __init__(self):
        self.threads = {}  # thread id -> {message id -> message}

    def clear(self):
        self.threads = {}

    def add(self, messages):
        """Merge messages; returns the ids of the threads that changed."""
        changed = set()
        for message in messages:
            thread = self.threads.setdefault(message["thread_id"], {})
            if thread.get(message["id"]) != message:
                thread[message["id"]] = message
                changed.add(message["thread_id"])
        return changed

    def messages(self, thread_id):
        return sorted(self.threads.get(thread_id, {}).values(), key=lambda m: (m["date"], m["id"]))

    def latest(self, thread_id):
        messages = self.messages(thread_id)
        return messages[-1] if messages else None

    def oldest_date(self, thread_id):
        messages = self.messages(thread_id)
        return messages[0]["date"] if messages else 0

    def conversations(self):
        """Latest message of every thread, newest first."""
        latest = [self.latest(t) for t in self.threads]
        return sorted((m for m in latest if m), key=lambda m: m["date"], reverse=True)

    def thread_for(self, addresses):
        """An existing thread with exactly these people, or None."""
        wanted = address_set(addresses)
        for message in self.conversations():
            if address_set(message["addresses"]) == wanted:
                return message["thread_id"]
        return None
