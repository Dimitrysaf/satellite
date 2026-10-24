project(
  'satellite',
  version: '0.1.0',
  meson_version: '>= 0.59.0',
)

prefix = get_option('prefix')
datadir = get_option('datadir')
bindir = get_option('bindir')
satellite_dir = datadir / 'satellite'
daemon_dir = satellite_dir / 'daemon'

python = import('python').find_installation('python3')
python_module_dir = python.get_install_dir()
if prefix == '/usr'
  # Debian's system Python uses dist-packages instead of site-packages.
  python_module_dir = join_paths(prefix, 'lib/python3/dist-packages')
endif

install_data(
  'satellite/__init__.py',
  'satellite/mpris_bridge.py',
  install_dir: python_module_dir / 'satellite',
)
install_data(
  'satellite/plugins/__init__.py',
  'satellite/plugins/_base.py',
  install_dir: python_module_dir / 'satellite' / 'plugins',
)

plugin_ids = [
  'battery', 'clipboard', 'connectivity_report', 'contacts', 'lock',
  'mousepad', 'mpris', 'notifications', 'ping', 'presenter', 'ring',
  'runcommand', 'sftp', 'share', 'sms', 'systemvolume', 'telephony',
]
foreach plugin_id : plugin_ids
  install_data(
    join_paths('satellite', 'plugins', plugin_id, '__init__.py'),
    install_dir: python_module_dir / 'satellite' / 'plugins' / plugin_id,
  )
endforeach

versioned_daemon = custom_target(
  'versioned-daemon',
  input: 'daemon/daemon.py',
  output: 'daemon.py',
  command: [
    'sh', '-c',
    'sed "s/__PROJECT_VERSION__/' + meson.project_version() + '/g" "$1" > "$2"',
    'sh', '@INPUT@', '@OUTPUT@',
  ],
  install: true,
  install_dir: daemon_dir,
)
versioned_gui = custom_target(
  'versioned-satellite-gui',
  input: 'satellite/satellite.py',
  output: 'satellite.py',
  command: [
    'sh', '-c',
    'sed "s/__PROJECT_VERSION__/' + meson.project_version() + '/g" "$1" > "$2"',
    'sh', '@INPUT@', '@OUTPUT@',
  ],
  install: true,
  install_dir: python_module_dir / 'satellite',
)

launcher_conf = configuration_data()
launcher_conf.set('satellite_dir', join_paths(prefix, datadir, 'satellite'))
launcher_conf.set('daemon_dir', join_paths(prefix, datadir, 'satellite', 'daemon'))
launcher_conf.set('python_module_dir', python_module_dir)
launcher_conf.set('bindir', join_paths(prefix, bindir))

configure_file(
  input: 'data/satellite.in',
  output: 'satellite',
  configuration: launcher_conf,
  install: true,
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)
configure_file(
  input: 'data/satellite-daemon.in',
  output: 'satellite-daemon',
  configuration: launcher_conf,
  install: true,
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)

configure_file(
  input: 'data/org.x.Satellite.service.in',
  output: 'org.x.Satellite.service',
  configuration: launcher_conf,
  install_dir: datadir / 'dbus-1' / 'services',
)
configure_file(
  input: 'data/satellite-daemon.desktop.in',
  output: 'satellite-daemon.desktop',
  configuration: launcher_conf,
  install_dir: get_option('sysconfdir') / 'xdg' / 'autostart',
)

subdir('data')
subdir('po')

test_env = environment()
test_env.set('PYTHONPATH', meson.project_source_root())
test(
  'python-unit',
  python,
  args: ['-m', 'unittest', 'discover', '-s', meson.project_source_root() / 'tests'],
  env: test_env,
  workdir: meson.project_source_root(),
)
