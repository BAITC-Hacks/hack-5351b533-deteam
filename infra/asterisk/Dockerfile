FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends asterisk python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY docker/entrypoint.py /usr/local/bin/asterisk-entrypoint
RUN chmod +x /usr/local/bin/asterisk-entrypoint

EXPOSE 5060/udp 5038/tcp 8088/tcp 10000-10100/udp

ENTRYPOINT ["python3", "/usr/local/bin/asterisk-entrypoint"]
