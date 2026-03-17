from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class CloudDevice:
    id: str
    name: str
    state: str


class WiserCloudDriver:
    """Cloud fallback scaffold for Wiser Home Essential API.

    This is intentionally conservative: if required credentials are missing,
    the caller should emit a manual_action instruction instead of brute forcing.
    """

    def __init__(
        self,
        api_base: str,
        access_token: Optional[str],
        subscription_key: Optional[str],
    ) -> None:
        self.api_base = (api_base or "").strip()
        self.access_token = (access_token or "").strip()
        self.subscription_key = (subscription_key or "").strip()

    def is_configured(self) -> bool:
        return bool(self.api_base and self.access_token and self.subscription_key)

    def discover(self) -> List[CloudDevice]:
        return []

    def send_command(self, device_id: str, command: str) -> bool:
        return False

    def poll_states(self) -> Dict[str, str]:
        return {}
