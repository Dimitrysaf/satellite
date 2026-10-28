"""Searchable list of a device's plugins."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from satellite.ui.plugin_row import PluginRow
from satellite.i18n import _


class PluginList(Gtk.Box):
    def __init__(self, plugins, on_toggled, on_configure):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text(_("Search plugins"))
        for side in ("start", "end"):
            getattr(self._search, "set_margin_" + side)(16)
        self._search.set_margin_top(4)
        self._search.set_margin_bottom(4)
        self.pack_start(self._search, False, False, 0)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.get_style_context().add_class(Gtk.STYLE_CLASS_VIEW)
        self._list.set_filter_func(self._filter, None)
        self._search.connect("search-changed", lambda _e: self._list.invalidate_filter())
        for plugin in plugins:
            self._list.add(PluginRow(plugin, on_toggled, on_configure))
        none = Gtk.Label(label=_("No matching plugins"))
        none.get_style_context().add_class("dim-label")
        none.set_margin_top(16)
        none.set_margin_bottom(16)
        none.show()
        self._list.set_placeholder(none)
        self.pack_start(self._list, False, True, 0)

    def _filter(self, row, _data):
        query = self._search.get_text().strip().casefold()
        return not query or query in row.search_text
