# Satellite

Satellite is a GTK 3 desktop client for KDE Connect compatible devices. It
discovers devices on the local network, pairs over the KDE Connect TLS
protocol, and keeps device links active through a background daemon.

## Features

- Device discovery, pairing requests, secure connections, and reconnects
- Battery status, ping, ring, clipboard, file sharing, and device controls
- Plugin capability negotiation and per-device plugin settings
- Incoming file transfers saved to `~/Downloads`
- Media controls and a desktop MPRIS bridge

Plugin support depends on the capabilities negotiated with the connected
device. The plugin modules live under `satellite/plugins/` and are loaded by
the daemon's `PluginManager`.

## Architecture

- `satellite/satellite.py` contains the GTK application and GUI entry point.
- `daemon/daemon.py` handles discovery, TLS connections, pairing, and packets.
- `satellite/` contains shared constants, the MPRIS bridge, and plugin modules.
- `data/` contains desktop integration files, the schema, icon, and launchers.

The `satellite` launcher starts the GUI. The `satellite-daemon` launcher starts
the background daemon used by the desktop D-Bus activation and autostart files.

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
XApp, and `xdotool` for remote keyboard and mouse input.

## License

Satellite is licensed under GPL-3.0-or-later. See [LICENSES/GPL-3.0.txt](LICENSES/GPL-3.0.txt).
