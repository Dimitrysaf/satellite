# Satellite

Satellite is a GTK 3 desktop client for KDE Connect compatible devices. It
discovers devices on the local network, pairs over the KDE Connect TLS
protocol, and keeps device links active through a background daemon.

## Features

- Device discovery, pairing requests, secure connections, and reconnects
- Main buttons on each device page: Ping, Find device, Send files, Send clipboard
- Send and receive files with progress popups, notifications and a configurable
  download folder; shared text and links from the phone
- Phone notifications, incoming and missed calls (with a Silence button), and
  low-battery warnings shown as desktop notifications
- Pause media while the phone rings, and resume afterwards
- Phone as touchpad, keyboard and presenter pointer (X11, with modifier chords
  such as Ctrl+C and a left-handed option)
- Lock this computer from the phone (unlocking is off unless you allow it)
- Share this computer's battery with the phone; show the phone's signal strength
- Run-command list the phone can trigger, edited in the plugin's own dialog
- Media controls and a desktop MPRIS bridge
- Tray icon and optional background mode, local device nicknames, preferences

Plugin support depends on the capabilities negotiated with the connected
device. Every plugin is one folder under `satellite/plugins/<id>/` and owns its
packet handling, its buttons, its status line and its own configuration dialog.

## Architecture

- `daemon/daemon.py` is the entry point for discovery, links and pairing; the
  protocol helpers it uses live in `satellite/tls.py`.
- `satellite/satellite.py` is only the `Gtk.Application`.
- `satellite/ui/` holds the interface, one class per module (`main_window`,
  `device_list`, `device_page`, `action_bar`, `plugin_list`, `transfer_tracker`, ...).
- `satellite/plugins/` holds the plugins. A plugin may provide `actions()`
  (buttons), `status()` (a status row) and `configure()` (its own dialog; import
  GTK lazily inside it so the daemon stays headless).
- `satellite/notifier.py` shows desktop notifications with clickable actions.
- `data/` holds desktop integration files, the schema, icon and launchers.

## Development

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -t .   # tests
python3 tools/preview.py                                  # window on mock devices
xvfb-run -a python3 tools/preview.py --shots /tmp/shots   # headless screenshots
```

## Build and install

Build with Meson:

```sh
meson setup build
meson compile -C build
meson install -C build
```

Build the Debian package from the repository root with:

```sh
dpkg-buildpackage -us -uc
```

The application requires Python 3, GTK 3 with PyGObject, OpenSSL bindings,
XApp, and `xdotool` for remote keyboard and mouse input. `canberra-gtk-play`
or `paplay` is used for the Find my device sound.

## License

Satellite is licensed under GPL-3.0-or-later. See [LICENSES/GPL-3.0.txt](LICENSES/GPL-3.0.txt).
