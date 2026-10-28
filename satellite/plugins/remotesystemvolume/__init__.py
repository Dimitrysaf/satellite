"""The paired device's volume levels (KDE Connect's remotesystemvolume)."""

from satellite.plugins._base import PacketStatePlugin
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True


class Plugin(PacketStatePlugin):
    plugin_id = "remotesystemvolume"
    display_name = _("Device volume")
    description = _("Receive the paired device's volume levels.")
    icon = "multimedia-volume-control-symbolic"
    incoming_capabilities = ("kdeconnect.systemvolume",)
    outgoing_capabilities = ("kdeconnect.systemvolume.request",)
    packet_types = incoming_capabilities
