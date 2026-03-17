from __future__ import annotations

import json
import logging
from typing import Callable, Optional

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)


class MQTTClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: Optional[str] = None,
        password: Optional[str] = None,
        base_topic: str = "wiser",
        discovery_prefix: str = "homeassistant",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.base_topic = base_topic.rstrip("/")
        self.discovery_prefix = discovery_prefix.rstrip("/")
        self.command_callback: Optional[Callable[[str, str], None]] = None

        self._uses_callback_v2 = hasattr(mqtt, "CallbackAPIVersion")
        if self._uses_callback_v2:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        else:
            self.client = mqtt.Client()
        self.client.enable_logger(_LOGGER)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

        if username:
            self.client.username_pw_set(username, password)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        self.client.will_set(
            f"{self.base_topic}/bridge/availability",
            payload="offline",
            qos=1,
            retain=True,
        )

    def connect(self) -> None:
        _LOGGER.info("Connecting to MQTT broker %s:%s", self.host, self.port)
        self.client.connect(self.host, self.port, keepalive=60)
        self.client.loop_start()
        self.publish_bridge_availability(True)

    def disconnect(self) -> None:
        self.publish_bridge_availability(False)
        self.client.loop_stop()
        self.client.disconnect()

    def is_connected(self) -> bool:
        return bool(self.client.is_connected())

    def set_command_callback(self, callback: Callable[[str, str], None]) -> None:
        self.command_callback = callback

    def publish_discovery(self, device_id: str, name: str) -> None:
        unique_id = f"wiser_{device_id}".replace(" ", "_").lower()
        state_topic = f"{self.base_topic}/{device_id}/state"
        command_topic = f"{self.base_topic}/{device_id}/set"
        availability_topic = f"{self.base_topic}/{device_id}/availability"

        payload = {
            "name": name,
            "unique_id": unique_id,
            "command_topic": command_topic,
            "state_topic": state_topic,
            "availability_topic": availability_topic,
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "ON",
            "state_off": "OFF",
            "retain": True,
            "availability_mode": "all",
            "device": {
                "identifiers": [f"wiser_bridge_{device_id}"],
                "manufacturer": "Schneider Electric",
                "model": "Wiser Relay",
                "name": name,
            },
        }

        topic = f"{self.discovery_prefix}/switch/{unique_id}/config"
        self.client.publish(topic, json.dumps(payload), qos=1, retain=True)
        _LOGGER.info("Published HA discovery for %s", device_id)

    def publish_state(self, device_id: str, state: str) -> None:
        topic = f"{self.base_topic}/{device_id}/state"
        self.client.publish(topic, payload=state, qos=1, retain=True)

    def publish_availability(self, device_id: str, online: bool) -> None:
        payload = "online" if online else "offline"
        topic = f"{self.base_topic}/{device_id}/availability"
        self.client.publish(topic, payload=payload, qos=1, retain=True)

    def publish_bridge_availability(self, online: bool) -> None:
        payload = "online" if online else "offline"
        topic = f"{self.base_topic}/bridge/availability"
        self.client.publish(topic, payload=payload, qos=1, retain=True)

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata,
        flags,
        reason_code,
        properties=None,
    ) -> None:
        _LOGGER.info("MQTT connected with reason code: %s", reason_code)
        client.subscribe(f"{self.base_topic}/+/set", qos=1)

    def _on_disconnect(self, client: mqtt.Client, userdata, *args) -> None:
        reason_code = args[1] if len(args) >= 2 else (args[0] if args else "unknown")
        _LOGGER.warning("MQTT disconnected with reason code: %s", reason_code)

    def _on_message(self, client: mqtt.Client, userdata, message: mqtt.MQTTMessage) -> None:
        try:
            payload = message.payload.decode("utf-8").strip().upper()
            parts = message.topic.split("/")
            if len(parts) < 3:
                return

            device_id = parts[-2]
            if self.command_callback:
                self.command_callback(device_id, payload)
        except Exception as exc:
            _LOGGER.error("Failed to process MQTT message on %s: %s", message.topic, exc)
