"""SMS caching and sending, contacts sync, vCard reading and the Messages model."""

import base64
import datetime
import os
import tempfile
import unittest
from unittest import mock

from satellite.plugins.contacts import Plugin as Contacts, contacts_dir
from satellite.plugins.sms import Plugin as Sms, clean_message
from satellite.vcard import Directory, number_key, parse
from satellite_sms.model import People, Store, day_label, list_time
from tests.fakes import LINK, FakeDaemon, packet


def phone_message(thread, uid, body, kind=1, date=1000, address="+30 694 123 4567", **extra):
    return dict({"thread_id": thread, "_id": uid, "body": body, "type": kind, "date": date,
                 "read": 1, "addresses": [{"address": address}]}, **extra)


class SmsPluginTests(unittest.TestCase):
    def test_messages_are_cached_by_thread_and_announced(self):
        plugin, daemon = Sms(), FakeDaemon()
        plugin.handle_packet(daemon, LINK, packet("kdeconnect.sms.messages", {"messages": [
            phone_message(1, 1, "old", date=1), phone_message(1, 2, "new", kind=2, date=5),
            phone_message(2, 3, "other", date=3), {"thread_id": "bad"}]}))
        self.assertEqual([m["body"] for m in plugin.conversations("dev1")], ["new", "other"])
        self.assertEqual([m["direction"] for m in plugin.thread("dev1", 1)], ["received", "sent"])
        self.assertEqual(daemon.events[0][0], "sms.messages")
        self.assertEqual(len(daemon.events[0][2]), 3)

    def test_clean_message_keeps_attachments_safe(self):
        message = clean_message(phone_message(1, 1, "", attachments=[
            {"part_id": 7, "mime_type": "image/jpeg", "unique_identifier": "../../evil.jpg"}]))
        self.assertEqual(message["attachments"][0]["unique_identifier"], "evil.jpg")
        self.assertFalse(clean_message(phone_message(1, 1, "x", read="0"))["read"])

    def test_send_text_and_picture(self):
        daemon = FakeDaemon()
        with tempfile.NamedTemporaryFile(suffix=".png") as picture:
            picture.write(b"\x89PNG data")
            picture.flush()
            self.assertTrue(Sms.send_message(daemon, "dev1", [" +30 1 ", "+30 2"], "Hi", [picture.name]))
        _plugin, _device, packet_type, body = daemon.sent[0]
        self.assertEqual(packet_type, "kdeconnect.sms.request")
        self.assertEqual(body["addresses"], [{"address": "+30 1"}, {"address": "+30 2"}])
        self.assertEqual(body["messageBody"], "Hi")
        self.assertEqual(body["attachments"][0]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(body["attachments"][0]["base64EncodedFile"]), b"\x89PNG data")

    def test_nothing_to_send(self):
        daemon = FakeDaemon()
        self.assertFalse(Sms.send_message(daemon, "dev1", [], "Hi"))
        self.assertFalse(Sms.send_message(daemon, "dev1", ["+30 1"], ""))
        self.assertFalse(Sms.send_message(daemon, "dev1", ["+30 1"], "Hi", ["/missing.png"]))
        self.assertEqual(daemon.sent, [])

    def test_thread_request_paging(self):
        daemon = FakeDaemon()
        Sms.request_conversation(daemon, "dev1", 4, start=1234, count=50)
        self.assertEqual(daemon.sent[0][3], {"threadID": 4, "rangeStartTimestamp": 1234, "numberToRequest": 50})


class FakeSharer:
    def __init__(self):
        self.calls = []

    def share(self, device_id, name, directory, to_eds, to_kpeople):
        self.calls.append(("share", device_id, name, to_eds, to_kpeople))

    def unshare(self, device_id, eds, kpeople):
        self.calls.append(("unshare", device_id, eds, kpeople))


class ContactsSyncTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_DATA_HOME": self.data.name})
        self.env.start()
        self.folder = contacts_dir("dev1")
        os.makedirs(self.folder)

    def tearDown(self):
        self.env.stop()
        self.data.cleanup()

    def plugin(self):
        self.sharer = FakeSharer()
        self.contacts = Contacts(sharer=self.sharer)
        return self.contacts

    def write(self, uid, timestamp):
        with open(os.path.join(self.folder, uid + ".vcf"), "w") as stream:
            stream.write("BEGIN:VCARD\nFN:{}\nX-KDECONNECT-TIMESTAMP:{}\nEND:VCARD\n".format(uid, timestamp))

    def test_requests_new_and_changed_and_deletes_removed(self):
        self.write("same", 5)
        self.write("changed", 1)
        self.write("gone", 1)
        daemon = FakeDaemon()
        self.plugin().handle_packet(daemon, LINK, packet("kdeconnect.contacts.response_uids_timestamps", {
            "uids": ["same", "changed", "new", "../escape"], "same": 5, "changed": 2, "new": 9,
            "../escape": 1}))
        self.assertEqual(daemon.sent[0][3], {"uids": ["changed", "new"]})
        self.assertEqual(sorted(os.listdir(self.folder)), ["changed.vcf", "same.vcf"])

    def test_vcards_are_written_and_announced(self):
        daemon = FakeDaemon()
        self.plugin().handle_packet(daemon, LINK, packet("kdeconnect.contacts.response_vcards", {
            "uids": ["a", "../b"], "a": "BEGIN:VCARD\nFN:Ann\nEND:VCARD", "../b": "x"}))
        self.assertEqual(os.listdir(self.folder), ["a.vcf"])
        self.assertEqual(daemon.events, [("contacts.changed", "dev1", None)])

    def test_new_vcards_are_shared_with_other_apps(self):
        daemon = FakeDaemon(settings={"contacts": {"share_kde": True}},
                            devices={"dev1": {"id": "dev1", "name": "Pixel 7"}})
        self.plugin().handle_packet(daemon, LINK, packet("kdeconnect.contacts.response_vcards",
                                                         {"uids": ["a"], "a": "x"}))
        self.contacts.share_thread.join(5)
        self.assertEqual(self.sharer.calls, [("share", "dev1", "Pixel 7", True, True),
                                             ("unshare", "dev1", False, False)])

    def test_sharing_switched_off_removes_the_copies(self):
        daemon = FakeDaemon(settings={"contacts": {"share_desktop": False}})
        plugin = self.plugin()
        plugin.settings_changed(daemon, "dev1")
        plugin.share_thread.join(5)
        self.assertEqual(self.sharer.calls[-1], ("unshare", "dev1", True, True))

    def test_switched_off(self):
        daemon = FakeDaemon(settings={"contacts": {"receive": False}})
        self.plugin().handle_packet(daemon, LINK, packet("kdeconnect.contacts.response_vcards",
                                                      {"uids": ["a"], "a": "x"}))
        self.assertEqual(os.listdir(self.folder), [])


class VCardTests(unittest.TestCase):
    def test_android_vcard_21(self):
        contact = parse(
            "BEGIN:VCARD\nVERSION:2.1\n"
            "FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=CE=93=CE=B9=CF=8E=\n=CF=81=CE=B3=CE=BF=CF=82\n"
            "TEL;CELL:+30 694 123 4567\nTEL;WORK;VOICE:210 555 0100\n"
            "PHOTO;ENCODING=BASE64;JPEG:/9j/4AAQ\n SkZJRg==\n\nEND:VCARD")
        self.assertEqual(contact.name, "Γιώργος")
        self.assertEqual(contact.phones, [("Mobile", "+30 694 123 4567"), ("Work", "210 555 0100")])
        self.assertEqual(contact.photo[:3], b"\xff\xd8\xff")

    def test_vcard_30_structured_name_and_escapes(self):
        contact = parse("BEGIN:VCARD\nVERSION:3.0\nN:Doe;Jane;;Dr.;\nitem1.EMAIL;TYPE=INTERNET:j@x.org\n"
                        "NOTE:Line one\\nLine two\\, really\nEND:VCARD")
        self.assertEqual(contact.name, "Dr. Jane Doe")
        self.assertEqual(contact.emails, [("Email", "j@x.org")])
        self.assertEqual(contact.note, "Line one\nLine two, really")

    def test_numbers_match_across_formats(self):
        self.assertEqual(number_key("+30 694 123 4567"), number_key("0030-694-1234567"))
        self.assertEqual(number_key("6941234567"), number_key("+306941234567"))
        self.assertEqual(number_key("ACS"), "acs")

    def test_directory_lookup(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "dev1"))
            with open(os.path.join(root, "dev1", "1.vcf"), "w") as stream:
                stream.write("BEGIN:VCARD\nFN:Maria\nTEL:+30 694 123 4567\nEND:VCARD")
            directory = Directory(root)
            directory.load()
            self.assertEqual(directory.find_number("6941234567").name, "Maria")
            self.assertEqual(People(directory).title(["6941234567", "+44 20 7946 0958"]),
                             "Maria, +44 20 7946 0958")


class MessagesModelTests(unittest.TestCase):
    def test_store(self):
        store = Store()
        changed = store.add([clean_message(phone_message(1, 1, "a", date=1)),
                             clean_message(phone_message(2, 2, "b", date=5, address="+30 2"))])
        self.assertEqual(changed, {1, 2})
        self.assertEqual(store.add([clean_message(phone_message(1, 1, "a", date=1))]), set())
        self.assertEqual([m["thread_id"] for m in store.conversations()], [2, 1])
        self.assertEqual(store.thread_for(["0030 2"]), None)  # too short to match loosely
        self.assertEqual(store.thread_for(["+30 694 123 4567"]), 1)

    def test_time_labels(self):
        now = datetime.datetime(2026, 10, 28, 15, 0)
        at = lambda **delta: (now - datetime.timedelta(**delta)).timestamp() * 1000  # noqa: E731
        self.assertEqual(list_time(at(hours=1), now), "14:00")
        self.assertEqual(list_time(at(days=1), now), "Yesterday")
        self.assertEqual(day_label(at(days=0), now), "Today")
        self.assertEqual(list_time(at(days=400), now), "23/09/2025")


class ShareTests(unittest.TestCase):
    ANDROID = ("BEGIN:VCARD\nVERSION:2.1\nN:Doe;Jane;;;\nFN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:"
               "=CE=96=CE=AE=CE=BD=CE=B1 Doe\nTEL;CELL:+30 694\nNOTE:a, b; c\n"
               "X-KDECONNECT-TIMESTAMP:7\nEND:VCARD")

    def test_vcard30_round_trip(self):
        from satellite.vcard import to_vcard30
        original = parse(self.ANDROID, "u1")
        text = to_vcard30(original)
        self.assertIn("VERSION:3.0", text)
        self.assertIn("NOTE:a\\, b\\; c", text)
        again = parse(text, "u1")
        self.assertEqual((again.name, again.phones, again.note, again.timestamp, again.structured),
                         (original.name, original.phones, original.note, 7, original.structured))

    def test_kde_folder_mirror_and_export(self):
        from satellite import contacts_share
        with tempfile.TemporaryDirectory() as data, mock.patch.dict(os.environ, {"XDG_DATA_HOME": data}):
            contacts = [parse(self.ANDROID, "u1"), parse("BEGIN:VCARD\nFN:Bob\nEND:VCARD", "u2")]
            contacts_share.mirror_kpeople("dev1", contacts)
            folder = contacts_share.kpeople_dir("dev1")
            self.assertEqual(sorted(os.listdir(folder)), ["u1.vcf", "u2.vcf"])
            contacts_share.mirror_kpeople("dev1", contacts[:1])  # Bob deleted on the phone
            self.assertEqual(os.listdir(folder), ["u1.vcf"])
            path = os.path.join(data, "all.vcf")
            contacts_share.export(contacts, path)
            with open(path) as stream:
                self.assertEqual(stream.read().count("BEGIN:VCARD"), 2)
            contacts_share.remove_kpeople("dev1")
            self.assertFalse(os.path.exists(folder))


class LaunchTests(unittest.TestCase):
    def test_source_checkout_runs_dotted_modules(self):
        from satellite import launch
        with mock.patch.object(launch.shutil, "which", lambda _name: None), \
                mock.patch.object(launch.subprocess, "Popen") as popen:
            self.assertTrue(launch.launch("satellite", "satellite.satellite", "--background"))
        self.assertEqual(popen.call_args[0][0][1:], ["-m", "satellite.satellite", "--background"])

    def test_installed_launcher_is_preferred(self):
        from satellite import launch
        with mock.patch.object(launch.shutil, "which", lambda name: "/usr/bin/" + name), \
                mock.patch.object(launch.subprocess, "Popen") as popen:
            launch.launch("satellite-sms", "satellite_sms", "--to", "123")
        self.assertEqual(popen.call_args[0][0], ["/usr/bin/satellite-sms", "--to", "123"])


class BackgroundStartTests(unittest.TestCase):
    def test_background_launch_never_pops_up_a_running_window(self):
        from satellite.satellite import SatelliteApplication
        app = SatelliteApplication()
        app.window = mock.Mock()
        app.start(hidden=True)  # what Messages/Contacts trigger to make sure Satellite runs
        app.window.present.assert_not_called()
        app.start(hidden=False)  # launching Satellite itself still shows it
        app.window.present.assert_called_once()

    def test_client_knows_at_once_that_satellite_runs(self):
        from satellite.phone_client import PhoneClient
        bus = mock.Mock()
        bus.call_sync.return_value.unpack.return_value = (True,)
        with mock.patch("satellite.phone_client.Gio.bus_watch_name_on_connection"):
            self.assertTrue(PhoneClient(bus).available)


if __name__ == "__main__":
    unittest.main()
