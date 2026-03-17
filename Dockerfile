ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

WORKDIR /app

COPY wiser_bridge /app
COPY run.sh /

RUN apk add --no-cache python3 py3-pip py3-paho-mqtt py3-requests
RUN python3 -m pip install --no-cache-dir aioWiserHeatAPI websocket-client
RUN chmod +x /run.sh
CMD ["/run.sh"]
