"""Share the clipboard with the paired device."""

from satellite.plugins._base import PacketStatePlugin, PluginAction
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "clipboard"
    display_name = _("Clipboard")
    description = _("Copy on one device, paste on the other.")
    icon = "edit-paste-symbolic"
    incoming_capabilities = ("kdeconnect.clipboard", "kdeconnect.clipboard.connect")
    outgoing_capabilities = ("kdeconnect.clipboard",)
    packet_types = incoming_capabilities
    settings_schema = (
        {"key": "receive", "label": _("Receive clipboard updates"), "type": "bool", "default": True},
        {"key": "send", "label": _("Send this computer's clipboard automatically"), "type": "bool", "default": True},
        {"key": "send_passwords", "label": _("Including passwords (as marked by password managers)"),
         "type": "bool", "default": True},
    )

    def actions(self, device):
        def run(ctx):
            text = ctx.clipboard_text()
            if not text:
                ctx.error(_("Clipboard is empty"), _("Copy some text first, then send it."))
            elif not ctx.daemon.send_plugin_packet(
                self.plugin_id, ctx.device_id, "kdeconnect.clipboard", {"content": text}
            ):
                ctx.error(_("Could not send the clipboard"), _("The connection is unavailable."))

        return [PluginAction(
            "send-clipboard", _("Send clipboard"), "edit-paste-symbolic", run,
            _("Send the current clipboard text to this device"),
        )]
