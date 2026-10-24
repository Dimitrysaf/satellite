"""Load and dispatch enabled KDE Connect plugins."""

import importlib
import json
import os
import pkgutil


class PluginManager:
    def __init__(self, config_dir):
        self.config_path = os.path.join(config_dir, "plugins.json")
        self.device_config_path = os.path.join(config_dir, "device-plugins.json")
        self.plugin_config_path = os.path.join(config_dir, "plugin-settings.json")
        self._modules = {}
        for item in pkgutil.iter_modules(__path__):
            if item.name.startswith("_"):
                continue
            module = importlib.import_module("{}.{}".format(__name__, item.name))
            if hasattr(module, "Plugin"):
                self._modules[item.name] = module
        self._enabled_ids = self._read_enabled_ids()
        self.plugins = {
            plugin_id: module.Plugin()
            for plugin_id, module in self._modules.items()
            if plugin_id in self._enabled_ids
        }
        self._device_enabled = self._read_json(self.device_config_path, {})
        self._settings = self._read_json(self.plugin_config_path, {})

    @staticmethod
    def _read_json(path, fallback):
        try:
            with open(path, "r", encoding="utf-8") as stream:
                value = json.load(stream)
            return value if isinstance(value, dict) else fallback
        except (OSError, ValueError):
            return fallback

    @staticmethod
    def _write_json(path, value):
        temporary = path + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)

    def _read_enabled_ids(self):
        defaults = {
            plugin_id
            for plugin_id, module in self._modules.items()
            if getattr(module, "ENABLED_BY_DEFAULT", False)
        }
        try:
            with open(self.config_path, "r", encoding="utf-8") as stream:
                config = json.load(stream)
            enabled = config.get("enabled", [])
            if isinstance(enabled, list):
                return {
                    item for item in enabled
                    if isinstance(item, str) and item in self._modules
                }
        except (OSError, ValueError, AttributeError):
            pass
        return defaults

    def set_enabled(self, plugin_id, enabled):
        """Persist a plugin toggle. Restart Satellite to renegotiate capabilities."""
        if plugin_id not in self._modules:
            raise KeyError("Unknown plugin: {}".format(plugin_id))
        enabled_ids = set(self._enabled_ids)
        if enabled:
            enabled_ids.add(plugin_id)
        else:
            enabled_ids.discard(plugin_id)
        temporary = self.config_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump({"enabled": sorted(enabled_ids)}, stream, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.config_path)
        self._enabled_ids = enabled_ids

    def is_enabled(self, plugin_id):
        return plugin_id in self.plugins

    def is_enabled_for(self, device_id, plugin_id):
        if plugin_id not in self.plugins:
            return False
        device_state = self._device_enabled.get(device_id, {})
        if not isinstance(device_state, dict):
            device_state = {}
        default = getattr(
            self.plugins[plugin_id], "ENABLED_FOR_DEVICE_BY_DEFAULT", True
        )
        return bool(device_state.get(plugin_id, default))

    def set_enabled_for(self, device_id, plugin_id, enabled):
        if plugin_id not in self.plugins:
            return False
        device_state = self._device_enabled.get(device_id)
        if not isinstance(device_state, dict):
            device_state = {}
            self._device_enabled[device_id] = device_state
        device_state[plugin_id] = bool(enabled)
        self._write_json(self.device_config_path, self._device_enabled)
        return True

    def get_settings(self, device_id, plugin_id):
        plugin = self.plugins.get(plugin_id)
        defaults = {
            field["key"]: field.get("default")
            for field in getattr(plugin, "settings_schema", ())
        } if plugin else {}
        device_settings = self._settings.get(device_id, {})
        plugin_settings = (
            device_settings.get(plugin_id, {})
            if isinstance(device_settings, dict) else {}
        )
        if isinstance(plugin_settings, dict):
            defaults.update(plugin_settings)
        return defaults

    def set_settings(self, device_id, plugin_id, settings):
        if plugin_id not in self.plugins:
            return False
        device_settings = self._settings.get(device_id)
        if not isinstance(device_settings, dict):
            device_settings = {}
            self._settings[device_id] = device_settings
        device_settings[plugin_id] = dict(settings)
        self._write_json(self.plugin_config_path, self._settings)
        return True

    def available_for(self, device_id, remote_incoming, remote_outgoing):
        available = []
        for plugin_id, plugin in self.plugins.items():
            if not plugin.is_supported(remote_incoming, remote_outgoing):
                continue
            available.append({
                "id": plugin_id,
                "name": plugin.display_name,
                "description": getattr(plugin, "description", ""),
                "icon": getattr(plugin, "icon", None),
                "settings_schema": list(getattr(plugin, "settings_schema", ())),
                "configurable": bool(getattr(plugin, "settings_schema", ())),
                "enabled": self.is_enabled_for(device_id, plugin_id),
            })
        return available

    @property
    def incoming_capabilities(self):
        return sorted({
            capability
            for plugin in self.plugins.values()
            for capability in plugin.incoming_capabilities
        })

    @property
    def outgoing_capabilities(self):
        return sorted({
            capability
            for plugin in self.plugins.values()
            for capability in plugin.outgoing_capabilities
        })

    def supported_names(self, remote_incoming, remote_outgoing):
        return [
            plugin.display_name
            for plugin in self.plugins.values()
            if plugin.is_supported(remote_incoming, remote_outgoing)
        ]

    def capability_enabled_for(self, device_id, capability):
        return any(
            capability in plugin.outgoing_capabilities
            and self.is_enabled_for(device_id, plugin_id)
            for plugin_id, plugin in self.plugins.items()
        )

    def handle_packet(self, daemon, link, packet):
        for plugin_id, plugin in self.plugins.items():
            if not self.is_enabled_for(link["id"], plugin_id):
                continue
            if plugin.handle_packet(daemon, link, packet):
                return True
        return False

    def send(self, plugin_id, daemon, device_id, **kwargs):
        plugin = self.plugins.get(plugin_id)
        if plugin is None or not self.is_enabled_for(device_id, plugin_id):
            return False
        settings = self.get_settings(device_id, plugin_id)
        settings.update({key: value for key, value in kwargs.items() if value is not None})
        return plugin.send(daemon, device_id, **settings)
