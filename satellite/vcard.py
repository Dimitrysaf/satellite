"""Read the vCards synced from the phone (versions 2.1, 3.0 and 4.0).

Android writes vCard 2.1: non-ASCII text is QUOTED-PRINTABLE with soft line
breaks, and photos are base64 folded over many lines. Only what the apps show
is kept.
"""

import base64
import os
import quopri
import re

from satellite.i18n import N_, _

# Labels stay in English here (they are written back to vCards); the apps
# translate them when they show them.
TYPE_LABELS = {
    "cell": N_("Mobile"), "home": N_("Home"), "work": N_("Work"), "main": N_("Main"), "fax": N_("Fax"),
    "pager": N_("Pager"), "voice": N_("Phone"), "other": N_("Other"), "internet": "", "pref": "",
}

# BDAY: 1990-05-21, 19900521, --05-21 or --0521 (no year), maybe followed by a time.
DATE = re.compile(r"^(?:(\d{4})|--)-?(\d{2})-?(\d{2})")
NO_YEAR = 1604  # what some phones write when the year isn't known
SAMPLE_YEAR = 1988  # a leap year (for 29 February) whose digits can't be a day or month


def parse_date(value):
    """(year or None, month, day) of a vCard date, or None if it isn't one."""
    match = DATE.match(value.strip())
    if not match:
        return None
    year = int(match.group(1)) if match.group(1) else None
    month, day = int(match.group(2)), int(match.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return (None if year == NO_YEAR else year), month, day


def format_date(value):
    """A vCard date in the locale's own date format; without a year, the same minus the year."""
    parsed = parse_date(value)
    if parsed is None:
        return value
    from gi.repository import GLib
    year, month, day = parsed
    try:
        text = GLib.DateTime.new_local(year or SAMPLE_YEAR, month, day, 0, 0, 0).format("%x")
    except (TypeError, AttributeError):  # an impossible date, like 31 February
        return value
    if year:
        return text
    for token in (str(SAMPLE_YEAR), str(SAMPLE_YEAR)[2:]):
        start = text.find(token)
        if start < 0:
            continue
        end = start + len(token)
        # Drop the separator that joined the year to the rest: "05/21/1988", "1988年05月21日".
        if end < len(text) and not text[end].isdigit():
            end += 1
        elif start > 0 and not text[start - 1].isdigit():
            start -= 1
        return (text[:start] + text[end:]).strip()
    return text


class Contact:
    def __init__(self, uid, device_id=""):
        self.uid = uid
        self.device_id = device_id
        self.name = ""
        self.organization = ""
        self.phones = []     # (label, number)
        self.emails = []     # (label, address)
        self.addresses = []  # (label, text)
        self.birthday = ""
        self.note = ""
        self.photo = None    # image bytes
        self.structured = []  # N: family; given; additional; prefixes; suffixes
        self.timestamp = None  # X-KDECONNECT-TIMESTAMP: when the phone last changed it

    @property
    def display_name(self):
        return self.name or (self.phones[0][1] if self.phones else "") or _("Unnamed")

    def __repr__(self):
        return "Contact({!r}, phones={!r})".format(self.display_name, self.phones)


def _unfold(text):
    """Join folded lines; vCard 2.1 also continues QP lines ending in '='."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif lines and lines[-1].endswith("=") and "QUOTED-PRINTABLE" in lines[-1].split(":", 1)[0].upper():
            lines[-1] = lines[-1][:-1] + raw
        elif raw.strip() and lines and _is_base64_tail(lines[-1], raw):
            lines[-1] += raw.strip()
        else:
            lines.append(raw)
    return [line for line in lines if line.strip()]


def _is_base64_tail(previous, raw):
    """vCard 2.1 photos continue on lines with no 'NAME:' prefix."""
    head = previous.split(":", 1)[0].upper()
    return ("ENCODING=B" in head or "ENCODING=BASE64" in head) and ":" not in raw


def _parse_line(line):
    if ":" not in line:
        return None
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].split(".")[-1].upper()  # drop "item1." groups
    params, types = {}, []
    for part in parts[1:]:
        if "=" in part:
            key, val = part.split("=", 1)
            key = key.upper()
            if key == "TYPE":
                types += [t.lower() for t in val.strip('"').split(",")]
            else:
                params[key] = val
        else:
            types.append(part.lower())  # vCard 2.1 bare types: TEL;CELL:
    encoding = params.get("ENCODING", "").upper()
    if encoding == "QUOTED-PRINTABLE" or "quoted-printable" in types:
        charset = params.get("CHARSET", "utf-8")
        value = quopri.decodestring(value.encode("latin-1", "replace")).decode(charset, "replace")
    return name, types, params, value


def _text(value):
    return re.sub(r"\\([,;\\])", r"\1", value).replace("\\n", "\n").replace("\\N", "\n").strip()


def _label(types):
    for kind in types:
        if kind in TYPE_LABELS and TYPE_LABELS[kind]:
            return TYPE_LABELS[kind]
        if kind.startswith("x-") and len(kind) > 2:
            return kind[2:].capitalize()
    return N_("Phone")


def parse(text, uid="", device_id=""):
    contact = Contact(uid, device_id)
    structured = ""
    for line in _unfold(text):
        parsed = _parse_line(line)
        if parsed is None:
            continue
        name, types, params, value = parsed
        if name == "FN":
            contact.name = _text(value)
        elif name == "N":
            fields = ([_text(f) for f in value.split(";")] + [""] * 5)[:5]
            contact.structured = fields
            structured = " ".join(f for f in (fields[3], fields[1], fields[2], fields[0], fields[4]) if f)
        elif name == "X-KDECONNECT-TIMESTAMP":
            try:
                contact.timestamp = int(value.strip())
            except ValueError:
                pass
        elif name == "TEL" and value.strip():
            contact.phones.append((_label(types), value.strip()))
        elif name == "EMAIL" and value.strip():
            contact.emails.append((_label([t for t in types if t != "internet"]) if types else N_("Email"),
                                   value.strip()))
        elif name == "ORG":
            contact.organization = " ".join(_text(f) for f in value.split(";") if f.strip())
        elif name == "BDAY":
            contact.birthday = value.strip()
        elif name == "NOTE":
            contact.note = _text(value)
        elif name == "ADR":
            parts = [_text(f) for f in value.split(";") if f.strip()]
            if parts:
                contact.addresses.append((_label(types), ", ".join(parts)))
        elif name == "PHOTO":
            contact.photo = _photo(value, params, types)
    if not contact.name:
        contact.name = structured
    for index, (label, address) in enumerate(contact.emails):
        if label == "Phone":
            contact.emails[index] = ("Email", address)
    return contact


def _photo(value, params, types):
    value = value.strip()
    if value.startswith("data:") and "," in value:
        value = value.split(",", 1)[1]
    elif params.get("VALUE", "").lower() == "uri":
        return None
    try:
        return base64.b64decode("".join(value.split()), validate=False) or None
    except (ValueError, TypeError):
        return None


LABEL_TYPES = {"Mobile": "CELL", "Home": "HOME", "Work": "WORK", "Main": "MAIN", "Fax": "FAX",
               "Pager": "PAGER", "Phone": "VOICE", "Other": "OTHER"}


def _escape(text):
    return (text or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _fold(line):
    """vCard lines are folded at 75 characters, continuing with a space."""
    parts = [line[:75]] + [" " + line[i:i + 74] for i in range(75, len(line), 74)]
    return "\r\n".join(parts)


def _type(label):
    return LABEL_TYPES.get(label, label.upper().replace(" ", "-") if label else "VOICE")


def to_vcard30(contact):
    """A clean vCard 3.0 for other address books (Evolution, KDE, Thunderbird).

    The UID and the phone's timestamp are kept, so later syncs can tell what
    changed.
    """
    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    if contact.uid:
        lines.append("UID:" + _escape(contact.uid))
    lines.append("FN:" + _escape(contact.display_name))
    family, given, additional, prefixes, suffixes = (contact.structured or ["", contact.display_name, "", "", ""])
    lines.append("N:" + ";".join(_escape(f) for f in (family, given, additional, prefixes, suffixes)))
    if contact.organization:
        lines.append("ORG:" + _escape(contact.organization))
    for label, number in contact.phones:
        lines.append("TEL;TYPE={}:{}".format(_type(label), _escape(number)))
    for label, address in contact.emails:
        kind = "INTERNET" if label in ("Email", "") else "INTERNET," + _type(label)
        lines.append("EMAIL;TYPE={}:{}".format(kind, _escape(address)))
    for label, text in contact.addresses:
        lines.append("ADR;TYPE={}:;;{};;;;".format(_type(label), _escape(text)))
    if contact.birthday:
        lines.append("BDAY:" + _escape(contact.birthday))
    if contact.note:
        lines.append("NOTE:" + _escape(contact.note))
    if contact.photo:
        kind = "PNG" if contact.photo[:4] == b"\x89PNG" else "JPEG"
        lines.append("PHOTO;ENCODING=b;TYPE={}:{}".format(
            kind, base64.b64encode(contact.photo).decode("ascii")))
    if contact.timestamp is not None:
        lines.append("X-KDECONNECT-TIMESTAMP:{}".format(contact.timestamp))
    lines.append("END:VCARD")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"

def load_directory(directory, device_id=""):
    contacts = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return contacts
    for name in names:
        if not name.endswith((".vcf", ".vcard")):
            continue
        try:
            with open(os.path.join(directory, name), "r", encoding="utf-8", errors="replace") as stream:
                contacts.append(parse(stream.read(), os.path.splitext(name)[0], device_id))
        except OSError:
            continue
    return contacts


def number_key(number):
    """Compare phone numbers loosely: the last 9 digits, ignoring formatting."""
    digits = re.sub(r"\D", "", number or "")
    return digits[-9:] if len(digits) >= 7 else (number or "").strip().lower()


class Directory:
    """Every synced contact, with lookup by phone number."""

    def __init__(self, root):
        self.root = root
        self.contacts = []
        self._by_number = {}

    def load(self, device_id=None):
        self.contacts = []
        try:
            devices = [device_id] if device_id else sorted(os.listdir(self.root))
        except OSError:
            devices = []
        for device in devices:
            self.contacts += load_directory(os.path.join(self.root, device), device)
        self.contacts.sort(key=lambda c: c.display_name.lower())
        self._by_number = {}
        for contact in self.contacts:
            for _label, number in contact.phones:
                self._by_number.setdefault(number_key(number), contact)
        return self.contacts

    def find_number(self, number):
        return self._by_number.get(number_key(number))
