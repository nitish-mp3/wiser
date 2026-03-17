ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

WORKDIR /app

COPY wiser_bridge /app
COPY run.sh /

RUN apk add --no-cache python3 py3-paho-mqtt py3-requests
RUN chmod +x /run.sh
CMD ["/run.sh"]
