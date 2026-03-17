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
        discovery_endpoints: Optional[List[str]] = None,
        debug_discovery: bool = False,
    ) -> None:
        if not hub_ip:
            raise ValueError("wiser_hub_ip is required")

        raw_hub = hub_ip.strip().rstrip("/")
        if raw_hub.startswith("http://") or raw_hub.startswith("https://"):
            self.base_urls = [raw_hub]
        else:
            self.base_urls = [f"http://{raw_hub}", f"https://{raw_hub}"]

        self.timeout_s = timeout_s
        self.retries = retries
        self.retry_delay_s = retry_delay_s
        self.debug_discovery = debug_discovery
        self.discovery_endpoints = discovery_endpoints or [
            "/api/devices",
            "/devices",
            "/api/v1/devices",
            "/api/v2/devices",
            "/gateway/devices",
            "/rest/devices",
            "/api/home/devices",
            "/api/system/devices",
        ]
        self._session = requests.Session()

    def discover(self) -> List[Device]:
        for endpoint in self.discovery_endpoints:
            payload = self._get_json(endpoint)
            if payload is None:
                continue

            devices = self._parse_device_list(payload)
            if devices:
                _LOGGER.info("Discovered %s devices from %s", len(devices), endpoint)
                return devices

        _LOGGER.warning("No devices discovered from Wiser hub across known endpoints")
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

        for item in self._iter_candidate_objects(payload):
            self._extract_state_from_item(item, state_map)

        return state_map

    def _extract_state_from_item(self, item: Any, state_map: Dict[str, str]) -> None:
        if not isinstance(item, dict):
            return

        device_id = self._extract_identifier(item)
        if not device_id:
            return

        raw_state = self._extract_raw_state(item)

        normalized = self._normalize_state(raw_state)
        if normalized:
            state_map[device_id] = normalized

    def _parse_device_list(self, payload: Any) -> List[Device]:
        devices: List[Device] = []

        for item in self._iter_candidate_objects(payload):
            if not isinstance(item, dict):
                continue

            if not self._is_switch_like(item):
                continue

            device_id = self._extract_identifier(item)
            if not device_id:
                continue

            name = self._extract_name(item, device_id)
            state = self._normalize_state(self._extract_raw_state(item)) or "OFF"
            devices.append(Device(id=device_id, type="switch", state=state, name=name))

        # De-duplicate by id while keeping first-seen order.
        deduped: List[Device] = []
        seen_ids = set()
        for device in devices:
            if device.id in seen_ids:
                continue
            seen_ids.add(device.id)
            deduped.append(device)

        return deduped

    def _iter_candidate_objects(self, payload: Any) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                candidates.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return candidates

    def _is_switch_like(self, item: Dict[str, Any]) -> bool:
        text_fields = [
            item.get("type"),
            item.get("device_type"),
            item.get("kind"),
            item.get("category"),
            item.get("product"),
            item.get("model"),
            item.get("name"),
        ]
        joined = " ".join(str(field).lower() for field in text_fields if field is not None)

        switch_tokens = ["switch", "relay", "socket", "outlet", "plug"]
        if any(token in joined for token in switch_tokens):
            return True

        # If shape looks like controllable stateful device, allow it.
        has_id = bool(self._extract_identifier(item))
        has_state = self._extract_raw_state(item) is not None
        return has_id and has_state

    def _extract_identifier(self, item: Dict[str, Any]) -> str:
        keys = ["id", "device_id", "serial", "serial_number", "uuid", "uid", "name"]
        for key in keys:
            value = item.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    def _extract_name(self, item: Dict[str, Any], fallback_id: str) -> str:
        for key in ["name", "label", "display_name", "friendly_name"]:
            value = item.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return f"Wiser {fallback_id}"

    def _extract_raw_state(self, item: Dict[str, Any]) -> Any:
        for key in ["state", "value", "status", "on", "is_on", "power"]:
            if key in item:
                return item.get(key)
        return None

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
        for base_url in self.base_urls:
            url = f"{base_url}{endpoint}"
            try:
                response = self._session.get(url, timeout=self.timeout_s)
                response.raise_for_status()
                payload = response.json()
                if self.debug_discovery:
                    _LOGGER.info(
                        "Discovery probe OK %s status=%s body=%s",
                        url,
                        response.status_code,
                        self._summarize_payload(payload),
                    )
                return payload
            except Exception as exc:
                if self.debug_discovery:
                    _LOGGER.info("Discovery probe failed %s error=%s", url, exc)
                _LOGGER.debug("GET %s failed: %s", url, exc)
                continue
        return None

    def _post_json(self, endpoint: str, payload: Dict[str, Any]) -> bool:
        for base_url in self.base_urls:
            url = f"{base_url}{endpoint}"
            try:
                response = self._session.post(url, json=payload, timeout=self.timeout_s)
                response.raise_for_status()
                return True
            except Exception as exc:
                _LOGGER.debug("POST %s failed payload=%s error=%s", url, payload, exc)
                continue
        return False

    def _summarize_payload(self, payload: Any) -> str:
        text = repr(payload)
        if len(text) > 400:
            return text[:400] + "..."
        return text
