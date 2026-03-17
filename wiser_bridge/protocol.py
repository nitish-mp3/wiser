from __future__ import annotations

import asyncio
import json
import logging
import re
import ssl
import time
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
import urllib3

try:
    import websocket
except Exception:  # pragma: no cover - optional runtime dependency
    websocket = None

try:
    from aioWiserHeatAPI.wiserhub import WiserAPI
except Exception:  # pragma: no cover - optional runtime dependency
    WiserAPI = None

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
        hub_port: int = 443,
        hub_secret: Optional[str] = None,
        timeout_s: float = 5.0,
        retries: int = 3,
        retry_delay_s: float = 1.0,
        discovery_endpoints: Optional[List[str]] = None,
        debug_discovery: bool = False,
        verify_tls: bool = False,
        ws_path: str = "/ws",
    ) -> None:
        if not hub_ip:
            raise ValueError("wiser_hub_ip is required")

        raw_hub = hub_ip.strip().rstrip("/")
        parsed = urlparse(raw_hub if "://" in raw_hub else f"https://{raw_hub}")
        self.hub_host = parsed.hostname or raw_hub
        self.hub_port = int(hub_port or parsed.port or 443)

        if raw_hub.startswith("http://") or raw_hub.startswith("https://"):
            self.base_urls = [raw_hub]
        else:
            self.base_urls = [
                f"http://{self.hub_host}:{self.hub_port}",
                f"https://{self.hub_host}:{self.hub_port}",
            ]

        self.timeout_s = timeout_s
        self.retries = retries
        self.retry_delay_s = retry_delay_s
        self.debug_discovery = debug_discovery
        self.verify_tls = verify_tls
        self.hub_secret = hub_secret or ""
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
        self.ws_path = ws_path if ws_path.startswith("/") else f"/{ws_path}"
        self._session = requests.Session()
        self._api = None
        if not self.verify_tls:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def discover(self) -> List[Device]:
        wiser_devices = self._discover_with_wiser_api()
        if wiser_devices:
            _LOGGER.info("Discovered %s devices via aioWiserHeatAPI", len(wiser_devices))
            return wiser_devices

        for endpoint in self.discovery_endpoints:
            payload = self._get_json(endpoint)
            if payload is None:
                continue

            devices = self._parse_device_list(payload)
            if devices:
                _LOGGER.info("Discovered %s devices from %s", len(devices), endpoint)
                return devices

        self._probe_metadata_endpoints()
        _LOGGER.warning("No devices discovered from Wiser hub across known endpoints")
        return []

    def send_command(self, device_id: str, state: str) -> bool:
        normalized = self._normalize_state(state)
        if normalized is None:
            _LOGGER.warning("Ignoring unsupported command payload for %s: %s", device_id, state)
            return False

        if self._send_command_with_wiser_api(device_id, normalized):
            return True

        if self._send_command_with_websocket(device_id, normalized):
            return True

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
        wiser_states = self._poll_states_with_wiser_api()
        if wiser_states:
            return wiser_states

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
                response = self._session.get(url, timeout=self.timeout_s, verify=self.verify_tls)
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

    def _probe_metadata_endpoints(self) -> None:
        probe_paths = [
            "/",
            "/index.html",
            "/app",
            "/api",
            "/api/",
            "/version",
            "/manifest.json",
            "/swagger",
            "/swagger-ui",
            "/openapi.json",
        ]

        for endpoint in probe_paths:
            for base_url in self.base_urls:
                url = f"{base_url}{endpoint}"
                try:
                    response = self._session.get(url, timeout=self.timeout_s, verify=self.verify_tls)
                    content_type = response.headers.get("Content-Type", "")
                    title = self._extract_html_title(response.text)
                    snippet = self._summarize_text(response.text)
                    location = response.headers.get("Location", "")
                    _LOGGER.info(
                        "Metadata probe %s status=%s content_type=%s location=%s title=%s body=%s",
                        url,
                        response.status_code,
                        content_type,
                        location,
                        title,
                        snippet,
                    )
                except Exception as exc:
                    if self.debug_discovery:
                        _LOGGER.info("Metadata probe failed %s error=%s", url, exc)

    def _extract_html_title(self, text: str) -> str:
        match = re.search(r"<title>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return " ".join(match.group(1).split())[:120]

    def _summarize_text(self, text: str) -> str:
        collapsed = " ".join(text.split())
        if len(collapsed) > 240:
            return collapsed[:240] + "..."
        return collapsed

    def _discover_with_wiser_api(self) -> List[Device]:
        api = self._get_wiser_api()
        if not api:
            return []

        devices: List[Device] = []
        for dev in self._iter_wiser_devices(api):
            device_id = self._wiser_device_id(dev)
            if not device_id:
                continue
            name = getattr(dev, "name", None) or f"Wiser {device_id}"
            state = "ON" if self._wiser_is_on(dev) else "OFF"
            devices.append(Device(id=device_id, type="switch", state=state, name=str(name)))

        deduped: List[Device] = []
        seen = set()
        for device in devices:
            if device.id in seen:
                continue
            seen.add(device.id)
            deduped.append(device)
        return deduped

    def _poll_states_with_wiser_api(self) -> Dict[str, str]:
        api = self._get_wiser_api(force_refresh=True)
        if not api:
            return {}

        state_map: Dict[str, str] = {}
        for dev in self._iter_wiser_devices(api):
            device_id = self._wiser_device_id(dev)
            if not device_id:
                continue
            state_map[device_id] = "ON" if self._wiser_is_on(dev) else "OFF"
        return state_map

    def _send_command_with_wiser_api(self, device_id: str, state: str) -> bool:
        api = self._get_wiser_api()
        if not api:
            return False

        target = self._get_wiser_device_by_id(api, device_id)
        if not target:
            return False

        try:
            if state == "ON" and hasattr(target, "turn_on"):
                asyncio.run(target.turn_on())
            elif state == "OFF" and hasattr(target, "turn_off"):
                asyncio.run(target.turn_off())
            else:
                return False

            # Refresh cached hub data after command.
            asyncio.run(api.read_hub_data())
            self._api = api
            _LOGGER.info("Command applied via aioWiserHeatAPI on %s -> %s", device_id, state)
            return True
        except Exception as exc:
            _LOGGER.debug("aioWiserHeatAPI command failed for %s: %s", device_id, exc)
            return False

    def _send_command_with_websocket(self, device_id: str, state: str) -> bool:
        if websocket is None:
            return False

        ws_url = f"wss://{self.hub_host}:{self.hub_port}{self.ws_path}"
        channel = self._extract_channel_from_device_id(device_id)
        state_num = 1 if state == "ON" else 0
        payloads = [
            json.dumps({"cmd": "set", "channel": channel, "state": state_num}),
            json.dumps({"action": "set", "channel": channel, "value": state_num}),
            json.dumps({"type": "set", "channel": channel, "on": state == "ON"}),
            json.dumps({"channel": channel, "state": state}),
        ]

        auth_payloads = []
        if self.hub_secret:
            auth_payloads = [
                self.hub_secret,
                json.dumps({"secret": self.hub_secret}),
                json.dumps({"token": self.hub_secret}),
                json.dumps({"auth": {"secret": self.hub_secret}}),
            ]

        try:
            ws = websocket.create_connection(
                ws_url,
                timeout=self.timeout_s,
                sslopt={"cert_reqs": ssl.CERT_NONE} if not self.verify_tls else {},
            )
            ws.settimeout(1)

            for ap in auth_payloads:
                try:
                    ws.send(ap)
                    _ = ws.recv()
                except Exception:
                    break

            for payload in payloads:
                try:
                    ws.send(payload)
                    resp = ws.recv()
                    resp_text = str(resp).upper()
                    if "FAIL" in resp_text:
                        continue
                    if any(ok in resp_text for ok in ["OK", "SUCCESS", "ON", "OFF", "TRUE", "FALSE"]) or resp_text:
                        ws.close()
                        _LOGGER.info("Command applied via websocket on %s -> %s", device_id, state)
                        return True
                except Exception:
                    continue

            ws.close()
        except Exception as exc:
            _LOGGER.debug("Websocket command failed for %s: %s", device_id, exc)
        return False

    def _extract_channel_from_device_id(self, device_id: str) -> int:
        digits = "".join(ch for ch in str(device_id) if ch.isdigit())
        if digits:
            return int(digits)
        return 1

    def _get_wiser_api(self, force_refresh: bool = False):
        if not self.hub_secret or WiserAPI is None:
            return None

        try:
            if self._api is None:
                self._api = WiserAPI(
                    host=self.hub_host,
                    port=self.hub_port,
                    secret=self.hub_secret,
                    extra_config_file="/data/wiser_custom_data",
                    enable_automations=False,
                )

            if force_refresh or self._api is not None:
                asyncio.run(self._api.read_hub_data())
            return self._api
        except Exception as exc:
            _LOGGER.debug("aioWiserHeatAPI connection failed: %s", exc)
            self._api = None
            return None

    def _iter_wiser_devices(self, api) -> List[Any]:
        devices: List[Any] = []
        containers = []

        if hasattr(api, "devices") and hasattr(api.devices, "all"):
            containers.append(api.devices.all)
        else:
            for name in ["smartplugs", "lights", "shutters", "power_tags_c"]:
                group = getattr(getattr(api, "devices", None), name, None)
                if group and hasattr(group, "all"):
                    containers.append(group.all)

        for c in containers:
            if not c:
                continue
            for dev in c:
                if self._wiser_is_switch_like(dev):
                    devices.append(dev)

        return devices

    def _wiser_is_switch_like(self, dev: Any) -> bool:
        type_text = " ".join(
            str(getattr(dev, key, ""))
            for key in ["product_type", "product_model", "model", "name"]
        ).lower()
        return any(token in type_text for token in ["plug", "switch", "relay", "socket", "light", "shutter"])

    def _wiser_device_id(self, dev: Any) -> str:
        for key in ["id", "device_type_id", "serial_number", "uuid", "name"]:
            value = getattr(dev, key, None)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    def _wiser_is_on(self, dev: Any) -> bool:
        for key in ["is_on", "current_state", "target_state", "manual_state", "output_state"]:
            value = getattr(dev, key, None)
            normalized = self._normalize_state(value)
            if normalized is not None:
                return normalized == "ON"
        return False

    def _get_wiser_device_by_id(self, api, device_id: str):
        devices = self._iter_wiser_devices(api)
        for dev in devices:
            if self._wiser_device_id(dev) == str(device_id):
                return dev
        return None

    def _post_json(self, endpoint: str, payload: Dict[str, Any]) -> bool:
        for base_url in self.base_urls:
            url = f"{base_url}{endpoint}"
            try:
                response = self._session.post(url, json=payload, timeout=self.timeout_s, verify=self.verify_tls)
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
