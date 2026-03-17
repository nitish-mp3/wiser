# Wiser MQTT Bridge Home Assistant Add-on

Wiser MQTT Bridge is a Home Assistant add-on that discovers Schneider Wiser relays and bridges them into Home Assistant using MQTT auto-discovery.

## What it does

- Discovers relay/switch devices from your Wiser hub
- Publishes Home Assistant MQTT discovery payloads
- Translates MQTT commands to hub commands
- Publishes retained state and availability topics
- Recovers state after restart with local state cache
- Retries hub commands and auto-recovers MQTT connection

## Repository layout

```text
addon/
├ config.yaml
├ Dockerfile
├ repository.yaml
├ run.sh
├ wiser_bridge/
│  ├ main.py
│  ├ protocol.py
│  └ mqtt.py
└ README.md
```

## Installation

1. Open Home Assistant.
2. Go to **Settings -> Add-ons -> Add-on Store**.
3. Add this repository URL:
   - `https://github.com/nitish-mp3/wiser`
4. Install **Wiser MQTT Bridge**.
5. Configure add-on options and start it.

## Configuration

```yaml
wiser_hub_ip: "auto"
wiser_hub_port: 443
wiser_hub_secret: ""
wiser_ws_path: "/ws"
relay_ids: "relay1,relay2,relay3,relay4"
relay_name_prefix: "Wiser Relay"
verify_hub_tls: false
cloud_api_base: ""
cloud_access_token: ""
cloud_subscription_key: ""
mqtt_host: "core-mosquitto"
mqtt_port: 1883
mqtt_user: ""
mqtt_password: ""
```

Required:
- `mqtt_host`
- `mqtt_port`

Optional:
- `wiser_hub_ip` (`auto` or explicit LAN IP, for example `192.168.1.50`)
- `wiser_hub_port` (hub API port; often `443` for newer hubs)
- `wiser_hub_secret` (hub secret/password used by local authenticated API)
- `wiser_ws_path` (local websocket path, default `/ws`)
- `verify_hub_tls` (`false` for self-signed local certs)
- `relay_ids` (comma-separated IDs shown in Home Assistant, for example `relay1,relay2`)
- `relay_name_prefix` (friendly name prefix for auto-created entities)
- `cloud_api_base` / `cloud_access_token` / `cloud_subscription_key` for cloud fallback
- `mqtt_user`
- `mqtt_password`

## Where to add devices

Use the add-on options page as the UI:

1. Open the add-on in Home Assistant.
2. Edit `relay_ids` with a comma-separated list.
3. Restart the add-on.

The bridge publishes MQTT discovery entries for those IDs even if hub auto-discovery is temporarily unavailable.

## MQTT topics

Discovery topic example:

```text
homeassistant/switch/wiser_relay1/config
```

Command topic:

```text
wiser/relay1/set
```

State topic:

```text
wiser/relay1/state
```

Availability topics:

```text
wiser/bridge/availability
wiser/relay1/availability
```

## Reliability and production behavior

- MQTT reconnect with backoff
- Last will (`offline`) for bridge availability
- Retained device states for fast HA recovery
- Command retry to Wiser hub
- Heartbeat availability updates
- State cache persisted at `/data/state_cache.json`

## Security notes

- Keep hub communication inside your LAN
- Use MQTT auth when available
- Use TLS for broker if your deployment requires encrypted transport

## Future expansion

The code is modular so adapters can be expanded later:

- Zigbee
- Matter
- BLE
- Local RF gateway

Target layering:

1. Device drivers
2. Protocol adapters
3. Unified device model
4. MQTT
5. Home Assistant
