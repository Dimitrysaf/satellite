i18n = import('i18n')
gnome = import('gnome')

install_data(
  'org.x.satellite.gschema.xml',
  install_dir: datadir / 'glib-2.0' / 'schemas',
)
install_data(
  'satellite.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps',
)

i18n.merge_file(
  input: 'org.x.Satellite.desktop.in',
  output: 'org.x.Satellite.desktop',
  type: 'desktop',
  po_dir: meson.project_source_root() / 'po',
  install: true,
  install_dir: datadir / 'applications',
)

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
)
