"""The translation template and catalogues: complete, valid and actually used."""

import ast
import gettext
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def read_po(path):
    """{(context, msgid): [msgstr, ...]} plus the plural msgids, from a PO/POT file."""
    entries, plurals = {}, {}
    context = msgid = plural = None
    strings, field = [], None

    def finish():
        if msgid is not None:
            entries[(context, msgid)] = strings
            if plural is not None:
                plurals[(context, msgid)] = plural

    def unquote(text):
        return ast.literal_eval(text)  # PO escapes are the same as Python's

    with open(path, encoding="utf-8") as stream:
        for line in stream.read().splitlines() + [""]:
            line = line.strip()
            if not line:
                finish()
                context = msgid = plural = None
                strings, field = [], None
            elif line.startswith("#"):
                continue
            elif line.startswith("msgctxt "):
                context, field = unquote(line[8:]), "context"
            elif line.startswith("msgid_plural "):
                plural, field = unquote(line[13:]), "plural"
            elif line.startswith("msgid "):
                msgid, field = unquote(line[6:]), "msgid"
            elif line.startswith("msgstr"):
                strings.append(unquote(line.split(" ", 1)[1]))
                field = "msgstr"
            elif line.startswith('"'):
                text = unquote(line)
                if field == "msgid":
                    msgid += text
                elif field == "plural":
                    plural += text
                elif field == "context":
                    context += text
                else:
                    strings[-1] += text
    entries.pop((None, ""), None)
    return entries, plurals


def languages():
    with open(os.path.join(ROOT, "po", "LINGUAS"), encoding="utf-8") as stream:
        return [line.strip() for line in stream if line.strip() and not line.startswith("#")]


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.template, self.plurals = read_po(os.path.join(ROOT, "satellite.pot"))

    def test_greek_is_listed(self):
        self.assertIn("el", languages())

    def test_every_language_translates_everything(self):
        for language in languages():
            with self.subTest(language=language):
                translations, _plurals = read_po(os.path.join(ROOT, "po", language + ".po"))
                missing = [key for key in self.template if not all(translations.get(key) or [""])]
                self.assertEqual(missing, [])

    def test_placeholders_and_lines_match(self):
        for language in languages():
            translations, _plurals = read_po(os.path.join(ROOT, "po", language + ".po"))
            for key, strings in translations.items():
                for text in strings:
                    with self.subTest(language=language, msgid=key[1]):
                        self.assertEqual(len(PLACEHOLDER.findall(text)), len(PLACEHOLDER.findall(key[1])))
                        self.assertEqual(text.count("\n"), key[1].count("\n"))

    @unittest.skipUnless(shutil.which("msgfmt"), "needs gettext's msgfmt")
    def test_catalogues_compile(self):
        for language in languages():
            with self.subTest(language=language):
                subprocess.run(["msgfmt", "--check", "-o", os.devnull,
                                os.path.join(ROOT, "po", language + ".po")], check=True)

    @unittest.skipUnless(shutil.which("xgettext"), "needs gettext's xgettext")
    def test_template_is_up_to_date(self):
        """Run ./makepot after changing interface text."""
        with tempfile.TemporaryDirectory() as folder:
            pot = os.path.join(folder, "satellite.pot")
            sources = sorted(
                os.path.relpath(os.path.join(directory, name), ROOT)
                for package in ("satellite", "satellite_sms", "satellite_contacts", "daemon")
                for directory, _dirs, files in os.walk(os.path.join(ROOT, package))
                for name in files if name.endswith(".py"))
            subprocess.run(["xgettext", "--language=Python", "--from-code=UTF-8", "--keyword=_",
                            "--keyword=N_", "--keyword=ngettext:1,2", "--keyword=pgettext:1c,2",
                            "--output=" + pot] + sources, cwd=ROOT, check=True)
            current, _plurals = read_po(pot)
        self.assertEqual(sorted(set(current) - set(self.template), key=str), [])


@unittest.skipUnless(shutil.which("msgfmt"), "needs gettext's msgfmt")
class GreekTests(unittest.TestCase):
    """The Greek catalogue, compiled and loaded the way Satellite loads it."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        target = os.path.join(cls.folder.name, "el", "LC_MESSAGES")
        os.makedirs(target)
        subprocess.run(["msgfmt", "-o", os.path.join(target, "satellite.mo"),
                        os.path.join(ROOT, "po", "el.po")], check=True)
        cls.greek = gettext.translation("satellite", cls.folder.name, languages=["el"])

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_words(self):
        self.assertEqual(self.greek.gettext("Preferences"), "Προτιμήσεις")
        self.assertEqual(self.greek.gettext("Send files…"), "Αποστολή αρχείων…")

    def test_plurals(self):
        self.assertEqual(self.greek.ngettext("{} contact", "{} contacts", 1).format(1), "1 επαφή")
        self.assertEqual(self.greek.ngettext("{} contact", "{} contacts", 5).format(5), "5 επαφές")

    def test_context(self):
        # The same English word, two Greek genders.
        self.assertEqual(self.greek.gettext("Normal"), "Κανονική")
        self.assertEqual(self.greek.pgettext("urgency", "Normal"), "Κανονικό")

    def test_satellite_finds_its_catalogue(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"SATELLITE_LOCALEDIR": self.folder.name, "LANGUAGE": "el"}):
            from satellite import i18n
            self.assertEqual(i18n.locale_dir(), self.folder.name)
            loaded = gettext.translation(i18n.DOMAIN, i18n.locale_dir(), fallback=True)
        self.assertEqual(loaded.gettext("Quit"), "Έξοδος")


if __name__ == "__main__":
    unittest.main()
