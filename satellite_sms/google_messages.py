"""Google Messages for web, embedded and locked to messages.google.com.

Optional (needs gir1.2-webkit2-4.1). Satellite only hosts Google's page: it
can't see or control what the page shows. The page gets its own private
storage folder, can't navigate the window anywhere but messages.google.com,
opens every other link in the user's browser and is denied the camera,
microphone, location and website notifications.
"""

import os
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk
from satellite.i18n import _

HOME = "https://messages.google.com/web/"
HOST = "messages.google.com"
# Google's own helper frames inside the page (sign-in checks, static files).
FRAME_DOMAINS = ("google.com", "gstatic.com", "googleusercontent.com", "googleapis.com")


def available():
    try:
        gi.require_version("WebKit2", "4.1")
        from gi.repository import WebKit2  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


def data_dirs():
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return (os.path.join(data, "satellite", "google-messages"),
            os.path.join(cache, "satellite", "google-messages"))


def delete_data():
    """Remove everything Google's page stored on this computer."""
    import shutil
    for folder in data_dirs():
        shutil.rmtree(folder, ignore_errors=True)


def _is_domain(host, domain):
    return host == domain or host.endswith("." + domain)


def navigation_policy(uri, main_frame, user_gesture, host=HOST, secure=True):
    """'allow', 'external' (open in the browser) or 'block' for a navigation.

    Only a link the user clicked ever opens the browser, so the page can't
    open tabs by itself. Google's own helper pages may load (in frames, or
    when the page itself moves there, as for signing in); nothing else does.
    """
    parts = urlparse(uri or "")
    scheme, target = parts.scheme.lower(), (parts.hostname or "").lower()
    if target == host and (scheme == "https" or (scheme == "http" and not secure)):
        return "allow"
    if scheme in ("about", "blob", "data") and not main_frame:
        return "allow"  # frames the page builds itself
    clicked = main_frame and user_gesture
    if scheme == "https" and any(_is_domain(target, d) for d in FRAME_DOMAINS):
        return "external" if clicked else "allow"
    if scheme in ("http", "https", "mailto", "tel", "sms"):
        return "external" if clicked else "block"
    return "block"  # javascript:, file:, plain-http messages.google.com, ...


class GoogleMessagesView(Gtk.Overlay):
    """The page plus a message for when it can't load."""

    def __init__(self, home=HOME, host=HOST, secure=True, dirs=None):
        super().__init__()
        gi.require_version("WebKit2", "4.1")
        from gi.repository import WebKit2
        self.WebKit2 = WebKit2
        self.home, self.host, self.secure = home, host, secure
        data_dir, cache_dir = dirs or data_dirs()
        for folder in (data_dir, cache_dir):
            os.makedirs(folder, mode=0o700, exist_ok=True)
        self.data_manager = WebKit2.WebsiteDataManager(
            base_data_directory=data_dir, base_cache_directory=cache_dir)
        self.data_manager.get_cookie_manager().set_persistent_storage(
            os.path.join(data_dir, "cookies.sqlite"), WebKit2.CookiePersistentStorage.SQLITE)
        context = WebKit2.WebContext.new_with_website_data_manager(self.data_manager)
        context.set_sandbox_enabled(True)
        context.connect("download-started", self._on_download)
        self.view = WebKit2.WebView(web_context=context)
        settings = self.view.get_settings()
        settings.set_enable_developer_extras(False)
        settings.set_javascript_can_open_windows_automatically(False)
        settings.set_user_agent_with_application_details("Satellite", "1")
        self.view.connect("decide-policy", self._on_decide_policy)
        self.view.connect("create", self._on_create)
        self.view.connect("permission-request", self._on_permission)
        self.view.connect("load-failed", self._on_load_failed)
        self.view.connect("load-failed-with-tls-errors", self._on_tls_error)
        self.view.connect("load-changed", self._on_load_changed)
        self.add(self.view)

        # Covers the whole page in the window's own colours, so it reads in
        # any theme whatever the page left behind.
        self.error = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.error.get_style_context().add_class("background")
        message = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                          halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        message.pack_start(Gtk.Image.new_from_icon_name("network-offline-symbolic", Gtk.IconSize.DIALOG),
                           False, False, 0)
        self.error_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=50)
        message.pack_start(self.error_label, False, False, 0)
        retry = Gtk.Button(label=_("Try again"), halign=Gtk.Align.CENTER)
        retry.connect("clicked", lambda _b: self.reload())
        message.pack_start(retry, False, False, 0)
        self.error.pack_start(message, True, True, 0)
        self.add_overlay(self.error)
        self._shown = False   # whether the page has displayed anything since loading
        self._status = None   # HTTP status of the latest page response
        self.show_all()
        self.error.hide()
        self.view.load_uri(self.home)

    # -- page ------------------------------------------------------------------

    def reload(self):
        self.error.hide()
        self._shown = False
        self.view.load_uri(self.home)

    def _on_decide_policy(self, _view, decision, kind):
        types = self.WebKit2.PolicyDecisionType
        if kind == types.RESPONSE:
            if decision.is_main_frame_main_resource():
                self._status = decision.get_response().get_status_code()
            return False
        if kind not in (types.NAVIGATION_ACTION, types.NEW_WINDOW_ACTION):
            return False
        action = decision.get_navigation_action()
        uri = action.get_request().get_uri()
        new_window = kind == types.NEW_WINDOW_ACTION
        # The main frame has no frame name; frames inside the page do.
        main_frame = new_window or decision.get_frame_name() is None
        policy = navigation_policy(uri, main_frame, action.is_user_gesture(), self.host, self.secure)
        if policy == "allow" and not new_window:
            decision.use()
        else:
            decision.ignore()  # never a second window inside Satellite
            if policy == "external" or (new_window and action.is_user_gesture()):
                open_externally(uri)
        return True

    @staticmethod
    def _on_create(_view, _action):
        return None  # new windows are handled (or refused) in decide-policy

    @staticmethod
    def _on_permission(_view, request):
        request.deny()  # camera, microphone, location, notifications, ...
        return True

    def _on_load_failed(self, _view, _event, uri, error):
        if error.matches(self.WebKit2.NetworkError.quark(), self.WebKit2.NetworkError.CANCELLED):
            return False
        if self._shown and error.matches(
                self.WebKit2.PolicyError.quark(), self.WebKit2.PolicyError.FRAME_LOAD_INTERRUPTED_BY_POLICY_CHANGE):
            return False  # e.g. a link that became a download: the page is still there
        self._show_error(load_error_text(self._status))
        return True

    def _on_tls_error(self, _view, uri, _certificate, _errors):
        # Never offer to continue: a bad certificate here means someone is in the way.
        self._show_error(_("The connection to Google Messages isn't secure, so Satellite stopped loading it."))
        return True

    def _on_load_changed(self, _view, event):
        if event == self.WebKit2.LoadEvent.STARTED:
            self._status = None
        elif event == self.WebKit2.LoadEvent.COMMITTED:
            self._shown = True
            self.error.hide()

    def _show_error(self, text):
        self.error_label.set_text(text)
        self.error.show()

    # -- files -----------------------------------------------------------------

    def _on_download(self, _context, download):
        download.connect("decide-destination", self._on_destination)

    @staticmethod
    def _on_destination(download, suggested):
        folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or GLib.get_home_dir()
        name = os.path.basename(suggested or "") or "download"
        stem, extension = os.path.splitext(name)
        path, number = os.path.join(folder, name), 1
        while os.path.exists(path):
            path, number = os.path.join(folder, "{} ({}){}".format(stem, number, extension)), number + 1
        download.set_destination(path)
        return True

    # -- signing out -----------------------------------------------------------

    def sign_out(self, done=None):
        """Forget the pairing: delete every cookie and stored site data."""
        def finished(manager, result):
            try:
                manager.clear_finish(result)
            except GLib.Error as error:
                print("Could not clear Google Messages data: {}".format(error.message), flush=True)
            if done:
                done()
        self.data_manager.clear(self.WebKit2.WebsiteDataTypes.ALL, 0, None, finished)


def load_error_text(status):
    if status and status >= 400:
        return (_("Google Messages couldn't be opened (error {}). Check this computer's internet "
                "connection, or try again later.").format(status))
    return _("Couldn't reach Google Messages. Check this computer's internet connection.")


def open_externally(uri):
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error as error:
        print("Could not open {}: {}".format(uri, error.message), flush=True)
