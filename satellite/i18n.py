"""Translations for Satellite and its companion apps (gettext domain "satellite").

``_("text")`` translates, ``ngettext(singular, plural, n)`` picks the plural
form, ``pgettext(context, "text")`` tells apart words that translate
differently depending on what they describe, and ``N_("text")`` only marks
text for translation where it is kept as data and translated later, when shown.

The catalogues are installed in ``<prefix>/share/locale``; that is found from
where this module is installed, so /usr and /usr/local installs both work.
``SATELLITE_LOCALEDIR`` overrides it (for running from a checkout).
"""

import gettext
import os

DOMAIN = "satellite"


def locale_dir():
    override = os.environ.get("SATELLITE_LOCALEDIR")
    if override:
        return override
    # <prefix>/lib/python3/dist-packages/satellite/i18n.py -> <prefix>/share/locale
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(here, "..", "..", "..", "..", "share", "locale"))
    return candidate if os.path.isdir(candidate) else None  # None: the system's default


_translation = gettext.translation(DOMAIN, locale_dir(), fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext
pgettext = _translation.pgettext  # the same words, translated differently by context


def N_(text):
    return text
