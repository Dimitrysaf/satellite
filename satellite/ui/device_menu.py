from satellite.i18n import _
"""The entries of a device's menu, shared by the device page and the tray.

Each entry is a :class:`MenuEntry` or ``None`` for a separator. ``controller``
is the main window; every callback takes no arguments.
"""


# Most used first; actions from plugins not listed here come last.
ACTION_ORDER = ("messages", "contacts", "send-files", "send-text", "send-clipboard", "browse",
                "ping", "ring", "remote-input")


def _action_rank(action):
    return ACTION_ORDER.index(action.id) if action.id in ACTION_ORDER else len(ACTION_ORDER)


class MenuEntry:
    def __init__(self, label, callback, opens_window=False, action=None, sensitive=True):
        self.label = label
        self.callback = callback
        # True when the entry shows a dialog, so the tray opens Satellite first.
        self.opens_window = opens_window
        self.action = action  # the PluginAction behind the entry, if any
        self.sensitive = sensitive


def device_entries(controller, device, include_pairing=True):
    """``include_pairing`` adds accept/reject/request entries; the device page
    leaves them out because its pairing prompt already has those buttons."""
    device_id = device["id"]
    entries = []
    if device.get("connected"):
        actions = [action for _plugin_id, action in controller.daemon.plugins.actions_for(device)]
        for action in sorted(actions, key=_action_rank):
            entries.append(MenuEntry(
                action.label, lambda a=action: controller.run_action(device_id, a),
                opens_window=action.opens_window, action=action))
    elif include_pairing and device.get("pair_requested"):
        code = device.get("verification_code")
        if code:
            entries.append(MenuEntry(code, None, sensitive=False))
        entries.append(MenuEntry(_("Accept"), lambda: controller.accept_pair(device_id)))
        entries.append(MenuEntry(_("Reject"), lambda: controller.deny_pair(device_id)))
    elif include_pairing and device.get("pair_pending"):
        entries.append(MenuEntry(_("Cancel pairing"), lambda: controller.cancel_pair(device_id)))
    elif include_pairing and device.get("secure") and not device.get("paired"):
        entries.append(MenuEntry(_("Request pairing"), lambda: controller.pair(device_id)))

    if device.get("secure"):
        if entries:
            entries.append(None)
        entries.append(MenuEntry(
            _("Encryption info"), lambda: controller.show_encryption(device_id), opens_window=True))
    if device.get("paired"):
        if entries:
            entries.append(None)
        entries.append(MenuEntry(_("Unpair"), lambda: controller.unpair(device_id), opens_window=True))
    return entries


def menu_signature(entries):
    """Changes whenever the visible menu would change."""
    return tuple(None if entry is None else (entry.label, entry.sensitive) for entry in entries)
