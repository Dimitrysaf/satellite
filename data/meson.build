i18n = import('i18n')
gnome = import('gnome')

install_data(
  'org.x.satellite.gschema.xml',
  install_dir: datadir / 'glib-2.0' / 'schemas',
)
install_data(
  'satellite.svg', 'satellite-sms.svg', 'satellite-contacts.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps',
)
# The tray icon, and the main window's hamburger (themes like Papirus draw open-menu as dots).
install_data(
  'satellite-symbolic.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'symbolic' / 'apps',
)
install_data(
  'satellite-menu-symbolic.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'symbolic' / 'actions',
)

# Satellite, and its companion apps Satellite Messages and Satellite Contacts.
foreach desktop_id : ['org.x.Satellite', 'org.x.Satellite.SMS', 'org.x.Satellite.Contacts']
  i18n.merge_file(
    input: desktop_id + '.desktop.in',
    output: desktop_id + '.desktop',
    type: 'desktop',
    po_dir: meson.project_source_root() / 'po',
    install: true,
    install_dir: datadir / 'applications',
  )
endforeach

# "Send to Phone" in Nemo's right-click menu (Edit → Plugins can switch it off).
i18n.merge_file(
  input: 'satellite-send.nemo_action.in',
  output: 'satellite-send.nemo_action',
  type: 'desktop',
  po_dir: meson.project_source_root() / 'po',
  install: true,
  install_dir: datadir / 'nemo' / 'actions',
)

# Lets the logged-in user create the virtual drawing tablet (/dev/uinput).
install_data(
  '60-satellite-uinput.rules',
  # Lets the logged-in user create a connected phone's system battery (/dev/uhid).
  '60-satellite-battery.rules',
  install_dir: get_option('prefix') / 'lib' / 'udev' / 'rules.d',
)
# Loads uhid at boot, so the logged-in user can open /dev/uhid (see 60-satellite-battery.rules).
install_data(
  'satellite-modules-load.conf',
  install_dir: get_option('prefix') / 'lib' / 'modules-load.d',
  rename: 'satellite.conf',
)

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
)
