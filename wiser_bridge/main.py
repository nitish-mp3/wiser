from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

import requests

from mqtt import MQTTClient
from protocol import Device, WiserHub

_LOGGER = logging.getLogger(__name__)
POLL_INTERVAL_S = 5
HEARTBEAT_INTERVAL_S = 60
STATE_CACHE_PATH = "/data/state_cache.json"


class StateCache:
    def __init__(self, path: str) -> None:
        self.path = path
        self._states: Dict[str, str] = {}

    def load(self) -> Dict[str, str]:
        if not os.path.exists(self.path):
            return {}

        try:
            with open(self.path, "r", encoding="utf-8") as cache_file:
                payload = json.load(cache_file)
            if isinstance(payload, dict):
                self._states = {str(k): str(v).upper() for k, v in payload.items()}
        except Exception as exc:
            _LOGGER.warning("Failed to load state cache: %s", exc)
        return dict(self._states)

    def save(self, states: Dict[str, str]) -> None:
        self._states.update(states)
        try:
            with open(self.path, "w", encoding="utf-8") as cache_file:
                json.dump(self._states, cache_file)
        except Exception as exc:
            _LOGGER.warning("Failed to save state cache: %s", exc)


class BridgeApp:
    def __init__(self, config: Dict[str, object]) -> None:
        self.running = True
        self.config = config

        self.mqtt = MQTTClient(
            host=str(config["mqtt_host"]),
            port=int(config["mqtt_port"]),
            username=str(config.get("mqtt_user") or "") or None,
            password=str(config.get("mqtt_password") or "") or None,
        )
        hub_ip = resolve_hub_ip(str(config.get("wiser_hub_ip") or ""))
        self.hub = WiserHub(hub_ip)
        self.cache = StateCache(STATE_CACHE_PATH)
        self.devices: List[Device] = []

        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

    def stop(self, signum, frame) -> None:
        _LOGGER.info("Stopping bridge (signal=%s)", signum)
        self.running = False

    def run(self) -> None:
        self._bootstrap()

        last_heartbeat = 0.0
        while self.running:
            now = time.time()
            self._poll_and_publish_states()

            if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                self._publish_heartbeat()
                last_heartbeat = now

            time.sleep(POLL_INTERVAL_S)

        self.mqtt.disconnect()

    def _bootstrap(self) -> None:
        self.mqtt.connect()
        self.mqtt.set_command_callback(self._handle_command)

        self.devices = self.hub.discover()
        if not self.devices:
            raise RuntimeError("No supported switch/relay devices discovered from Wiser hub")

        for device in self.devices:
            self.mqtt.publish_discovery(device.id, device.name)
            self.mqtt.publish_availability(device.id, True)

        cached_states = self.cache.load()
        for device in self.devices:
            if device.id in cached_states:
                self.mqtt.publish_state(device.id, cached_states[device.id])

    def _handle_command(self, device_id: str, payload: str) -> None:
        ok = self.hub.send_command(device_id, payload)
        if not ok:
            _LOGGER.warning("Command failed for %s with payload=%s", device_id, payload)
            return

        self.mqtt.publish_state(device_id, payload)
        self.cache.save({device_id: payload})

    def _poll_and_publish_states(self) -> None:
        states = self.hub.poll_states()
        if not states:
            return

        known_ids = {d.id for d in self.devices}
        published: Dict[str, str] = {}
        for device_id, state in states.items():
            if device_id not in known_ids:
                continue

            self.mqtt.publish_state(device_id, state)
            published[device_id] = state

        if published:
            self.cache.save(published)

    def _publish_heartbeat(self) -> None:
        self.mqtt.publish_bridge_availability(True)
        for device in self.devices:
            self.mqtt.publish_availability(device.id, True)


def load_config(path: str) -> Dict[str, object]:
    with open(path, "r", encoding="utf-8") as config_file:
        config = json.load(config_file)

    required_keys = ["mqtt_host", "mqtt_port"]
    missing = [key for key in required_keys if not config.get(key)]
    if missing:
        raise ValueError(f"Missing required configuration keys: {', '.join(missing)}")

    return config


def resolve_hub_ip(config_value: str) -> str:
    value = config_value.strip()
    if value and value.lower() != "auto":
        return value

    candidates = [
        "wiserhub.local",
        "wiser-hub.local",
        "wiser.local",
    ]

    for host in candidates:
        try:
            resolved = socket.gethostbyname(host)
            if resolved and _looks_like_wiser_hub(resolved):
                _LOGGER.info("Auto-discovered Wiser hub host %s -> %s", host, resolved)
                return resolved
        except Exception:
            continue

    probed = _discover_hub_on_lan()
    if probed:
        _LOGGER.info("Auto-discovered Wiser hub on LAN: %s", probed)
        return probed

    raise ValueError(
        "Could not auto-discover Wiser hub IP. Set wiser_hub_ip to the hub LAN IP (for example 192.168.1.50)."
    )


def _looks_like_wiser_hub(ip: str) -> bool:
    endpoints = ["/api/devices", "/devices", "/api/v1/devices"]
    for endpoint in endpoints:
        try:
            response = requests.get(f"http://{ip}{endpoint}", timeout=1.0)
            if response.status_code >= 400:
                continue

            data = response.json()
            if isinstance(data, list):
                return True
            if isinstance(data, dict):
                if isinstance(data.get("devices"), list):
                    return True
                if "id" in data and any(k in data for k in ("state", "value", "type")):
                    return True
        except Exception:
            continue
    return False


def _discover_hub_on_lan() -> str:
    local_ip = _get_local_ip()
    if not local_ip:
        return ""

    subnet_prefix = ".".join(local_ip.split(".")[:3])
    candidates = []

    candidates.extend(_read_arp_ips(prefix=subnet_prefix))

    for host in range(2, 255):
        ip = f"{subnet_prefix}.{host}"
        if ip == local_ip:
            continue
        candidates.append(ip)

    ordered_candidates = list(dict.fromkeys(candidates))

    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = {pool.submit(_looks_like_wiser_hub, ip): ip for ip in ordered_candidates}
        for future in as_completed(futures):
            ip = futures[future]
            try:
                if future.result():
                    return ip
            except Exception:
                continue

    return ""


def _get_local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("1.1.1.1", 53))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return ""


def _read_arp_ips(prefix: str) -> List[str]:
    arp_path = "/proc/net/arp"
    if not os.path.exists(arp_path):
        return []

    discovered: List[str] = []
    try:
        with open(arp_path, "r", encoding="utf-8") as arp_file:
            lines = arp_file.readlines()[1:]
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            ip = parts[0]
            if ip.startswith(prefix + "."):
                discovered.append(ip)
    except Exception:
        return []
    return discovered


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        _LOGGER.error("Usage: main.py <config_path>")
        return 2

    try:
        config = load_config(sys.argv[1])
        app = BridgeApp(config)
        app.run()
        return 0
    except Exception as exc:
        _LOGGER.exception("Bridge terminated due to error: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
