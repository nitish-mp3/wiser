from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from protocol import Device, WiserHub


@dataclass
class DriverResult:
    protocol: str
    devices: List[Device]


class WiserLocalDriver:
    """Thin local driver wrapper around the hub adapter used by the bridge."""

    def __init__(self, hub: WiserHub) -> None:
        self.hub = hub

    def discover(self) -> DriverResult:
        devices = self.hub.discover()
        protocol = "local-api-or-wss"
        return DriverResult(protocol=protocol, devices=devices)

    def handshake(self) -> bool:
        return True

    def send_command(self, device_id: str, command: str) -> bool:
        return self.hub.send_command(device_id, command)

    def poll_state(self, device_id: str) -> str:
        state_map = self.hub.poll_states()
        return state_map.get(device_id, "UNKNOWN")

    def poll_states(self) -> Dict[str, str]:
        return self.hub.poll_states()
