from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

_LOGGER = logging.getLogger(__name__)


@dataclass
class Device:
    id: str
    type: str
    state: str
    name: str


class WiserHub:
    """Adapter for Schneider Wiser hub HTTP APIs."""

    def __init__(
        self,
        hub_ip: str,
        timeout_s: float = 5.0,
        retries: int = 3,
        retry_delay_s: float = 1.0,
    ) -> None:
        if not hub_ip:
            raise ValueError("wiser_hub_ip is required")

        self.base_url = f"http://{hub_ip.strip()}"
        self.timeout_s = timeout_s
        self.retries = retries
        self.retry_delay_s = retry_delay_s
        self._session = requests.Session()

    def discover(self) -> List[Device]:
        endpoints = ["/api/devices", "/devices", "/api/v1/devices"]

        for endpoint in endpoints:
            payload = self._get_json(endpoint)
            if payload is None:
                continue

            devices = self._parse_device_list(payload)
            if devices:
                _LOGGER.info("Discovered %s devices from %s", len(devices), endpoint)
                return devices

        _LOGGER.warning("No devices discovered from Wiser hub")
        return []

    def send_command(self, device_id: str, state: str) -> bool:
        normalized = self._normalize_state(state)
        if normalized is None:
            _LOGGER.warning("Ignoring unsupported command payload for %s: %s", device_id, state)
            return False

        command_payloads = [
            {"id": device_id, "state": normalized},
            {"device_id": device_id, "value": normalized},
            {"state": normalized},
        ]
        endpoints = [
            f"/api/devices/{device_id}/command",
            f"/api/devices/{device_id}",
            f"/devices/{device_id}",
        ]

        for attempt in range(1, self.retries + 1):
            for endpoint in endpoints:
                for payload in command_payloads:
                    if self._post_json(endpoint, payload):
                        _LOGGER.info("Command applied on %s -> %s", device_id, normalized)
                        return True

            _LOGGER.warning(
                "Command retry %s/%s failed for %s",
                attempt,
                self.retries,
                device_id,
            )
            time.sleep(self.retry_delay_s)

        return False

    def poll_states(self) -> Dict[str, str]:
        endpoints = ["/api/states", "/api/devices", "/devices"]
        for endpoint in endpoints:
            payload = self._get_json(endpoint)
            if payload is None:
                continue

            states = self._extract_state_map(payload)
            if states:
                return states

        return {}

    def _extract_state_map(self, payload: Any) -> Dict[str, str]:
        state_map: Dict[str, str] = {}

        if isinstance(payload, list):
            for item in payload:
                self._extract_state_from_item(item, state_map)
            return state_map

        if isinstance(payload, dict):
            if "devices" in payload and isinstance(payload["devices"], list):
                for item in payload["devices"]:
                    self._extract_state_from_item(item, state_map)
            else:
                self._extract_state_from_item(payload, state_map)

        return state_map

    def _extract_state_from_item(self, item: Any, state_map: Dict[str, str]) -> None:
        if not isinstance(item, dict):
            return

        device_id = item.get("id") or item.get("device_id") or item.get("name")
        if not device_id:
            return

        raw_state = item.get("state")
        if raw_state is None:
            raw_state = item.get("value")

        normalized = self._normalize_state(raw_state)
        if normalized:
            state_map[str(device_id)] = normalized

    def _parse_device_list(self, payload: Any) -> List[Device]:
        devices: List[Device] = []

        if isinstance(payload, dict) and isinstance(payload.get("devices"), list):
            payload = payload["devices"]

        if not isinstance(payload, list):
            return devices

        for item in payload:
            if not isinstance(item, dict):
                continue

            device_type = str(item.get("type", "switch")).lower()
            if device_type not in {"switch", "relay"}:
                continue

            device_id = item.get("id") or item.get("device_id") or item.get("name")
            if not device_id:
                continue

            name = str(item.get("name") or f"Wiser {device_id}")
            state = self._normalize_state(item.get("state")) or "OFF"
            devices.append(Device(id=str(device_id), type="switch", state=state, name=name))

        return devices

    def _normalize_state(self, raw_state: Any) -> Optional[str]:
        if raw_state is None:
            return None

        value = str(raw_state).strip().upper()
        if value in {"ON", "1", "TRUE", "OPEN"}:
            return "ON"
        if value in {"OFF", "0", "FALSE", "CLOSED"}:
            return "OFF"
        return None

    def _get_json(self, endpoint: str) -> Optional[Any]:
        url = f"{self.base_url}{endpoint}"
        try:
            response = self._session.get(url, timeout=self.timeout_s)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            _LOGGER.debug("GET %s failed: %s", url, exc)
            return None

    def _post_json(self, endpoint: str, payload: Dict[str, Any]) -> bool:
        url = f"{self.base_url}{endpoint}"
        try:
            response = self._session.post(url, json=payload, timeout=self.timeout_s)
            response.raise_for_status()
            return True
        except Exception as exc:
            _LOGGER.debug("POST %s failed payload=%s error=%s", url, payload, exc)
            return False
