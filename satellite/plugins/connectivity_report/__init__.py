"""Connectivity report packet support."""

from satellite.plugins._base import PacketStatePlugin
from satellite.i18n import _

ENABLED_BY_DEFAULT = True
ENABLED_FOR_DEVICE_BY_DEFAULT = True

# Indexed by the 0-4 signal strength the device reports.
SIGNAL_ICONS = (
    "network-cellular-signal-none-symbolic",
    "network-cellular-signal-weak-symbolic",
    "network-cellular-signal-ok-symbolic",
    "network-cellular-signal-good-symbolic",
    "network-cellular-signal-excellent-symbolic",
)
SIGNAL_WORDS = (_("No signal"), _("Weak"), _("Fair"), _("Good"), _("Excellent"))


class Plugin(PacketStatePlugin):
    plugin_id = "connectivity_report"
    display_name = _("Connectivity report")
    description = _("Show mobile network signal reports from the device.")
    icon = "network-cellular-signal-excellent-symbolic"
    incoming_capabilities = ("kdeconnect.connectivity_report",)
    outgoing_capabilities = ("kdeconnect.connectivity_report.request",)
    packet_types = incoming_capabilities

    def status(self, device):
        data = device.get("plugin_data", {}).get(self.plugin_id, {}).get("body", {})
        strengths = data.get("signalStrengths")
        if not isinstance(strengths, dict) or not strengths:
            return None
        best = max(
            (item for item in strengths.values() if isinstance(item, dict)),
            key=lambda item: item.get("signalStrength", 0)
            if isinstance(item.get("signalStrength"), int) else 0,
            default=None,
        )
        if best is None:
            return None
        level = best.get("signalStrength")
        level = level if isinstance(level, int) and not isinstance(level, bool) else 0
        level = max(0, min(4, level))
        network = str(best.get("networkType") or _("Mobile"))
        return (SIGNAL_ICONS[level], "{} {}".format(network, SIGNAL_WORDS[level]))
