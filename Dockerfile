ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.11
FROM ${BUILD_FROM}

WORKDIR /app

COPY wiser_bridge /app
COPY run.sh /

RUN pip install --no-cache-dir paho-mqtt requests

CMD ["/run.sh"]
