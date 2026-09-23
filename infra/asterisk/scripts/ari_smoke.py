"""Small ARI controller for testing SIP -> AudioSocket -> SIP locally.

Run the AudioSocket echo server separately before placing a call. This program
only controls Asterisk channels; it does not process or synthesize audio.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from websocket import WebSocketConnectionClosedException, WebSocketTimeoutException
from websocket import create_connection


LOG = logging.getLogger("ari-smoke")


class AriError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Config:
    ari_url: str
    user: str
    password: str
    app: str
    audiosocket_host: str
    audiosocket_port: int

    @classmethod
    def from_env(cls) -> Config:
        password = os.environ.get("ARI_PASSWORD") or os.environ.get("ARI_SECRET")
        if not password:
            raise ValueError("Set ARI_PASSWORD (or ARI_SECRET) before starting")
        port = int(os.environ.get("AUDIOSOCKET_PORT", "9092"))
        if not 1 <= port <= 65535:
            raise ValueError("AUDIOSOCKET_PORT must be between 1 and 65535")
        return cls(
            ari_url=os.environ.get("ARI_URL", "http://127.0.0.1:8088"),
            user=os.environ.get("ARI_USER", "voice-ai"),
            password=password,
            app=os.environ.get("ARI_APP", "voice-ai"),
            audiosocket_host=os.environ.get("AUDIOSOCKET_HOST", "host.docker.internal"),
            audiosocket_port=port,
        )

    @property
    def base_url(self) -> str:
        value = self.ari_url.rstrip("/")
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("ARI_URL must be an http(s) URL")
        return value if parts.path.endswith("/ari") else value + "/ari"

    @property
    def events_url(self) -> str:
        parts = urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        query = urlencode({"app": self.app, "api_key": f"{self.user}:{self.password}"})
        return urlunsplit((scheme, parts.netloc, parts.path + "/events", query, ""))

    @property
    def media_host(self) -> str:
        host = self.audiosocket_host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{host}:{self.audiosocket_port}"


class AriClient:
    def __init__(self, config: Config):
        self.config = config
        token = base64.b64encode(f"{config.user}:{config.password}".encode()).decode()
        self.authorization = f"Basic {token}"

    def request(self, method: str, path: str, **params: str) -> dict | None:
        url = self.config.base_url + path
        if params:
            url += "?" + urlencode(params)
        request = Request(url, data=b"" if method == "POST" else None, method=method)
        request.add_header("Authorization", self.authorization)
        try:
            with urlopen(request, timeout=8) as response:
                body = response.read()
                return json.loads(body) if body else None
        except HTTPError as exc:
            detail = exc.read(256).decode("utf-8", "replace")
            raise AriError(f"{method} {path}: HTTP {exc.code}: {detail}", exc.code) from exc
        except OSError as exc:
            reason = exc.reason if isinstance(exc, URLError) else str(exc)
            raise AriError(f"{method} {path}: {reason}") from exc

    def discard(self, method: str, path: str, **params: str) -> None:
        """Best effort cleanup; an already removed channel/bridge is expected."""
        try:
            self.request(method, path, **params)
        except AriError as exc:
            if exc.status not in (404, 409):
                LOG.warning("Cleanup failed: %s", exc)


@dataclass
class Call:
    caller_id: str
    bridge_id: str
    external_id: str
    media_uuid: str
    transferring: bool = False


class SmokeController:
    def __init__(self, config: Config, ari: AriClient | None = None):
        self.config = config
        self.ari = ari or AriClient(config)
        self.calls: dict[str, Call] = {}
        self.external_to_caller: dict[str, str] = {}

    def handle(self, event: dict) -> None:
        event_type = event.get("type")
        channel = event.get("channel") or {}
        channel_id = channel.get("id")
        if not channel_id:
            return
        if event_type == "StasisStart":
            if channel_id in self.external_to_caller or channel.get("name", "").startswith("AudioSocket/"):
                return
            self.start_call(channel)
        elif event_type == "ChannelDtmfReceived":
            call = self.calls.get(channel_id)
            if call:
                self.on_digit(call, event.get("digit", ""))
        elif event_type in ("StasisEnd", "ChannelDestroyed"):
            if channel_id in self.calls:
                self.end_call(channel_id)
            elif channel_id in self.external_to_caller:
                caller_id = self.external_to_caller[channel_id]
                LOG.warning("AudioSocket channel ended for %s; closing call", caller_id)
                self.ari.discard("DELETE", f"/channels/{quote(caller_id, safe='')}")
                self.end_call(caller_id)

    def start_call(self, channel: dict) -> None:
        caller_id = channel["id"]
        if caller_id in self.calls:
            return
        call = Call(
            caller_id=caller_id,
            bridge_id=str(uuid.uuid4()),
            external_id=str(uuid.uuid4()),
            media_uuid=str(uuid.uuid4()),
        )
        self.calls[caller_id] = call
        self.external_to_caller[call.external_id] = caller_id
        caller_number = (channel.get("caller") or {}).get("number", "unknown")
        LOG.info("Call %s from %s; AudioSocket UUID %s", caller_id, caller_number, call.media_uuid)
        caller_path = f"/channels/{quote(caller_id, safe='')}"
        bridge_path = f"/bridges/{quote(call.bridge_id, safe='')}"
        try:
            if channel.get("state") != "Up":
                self.ari.request("POST", caller_path + "/answer")
            self.ari.request("POST", "/bridges", type="mixing,dtmf_events", bridgeId=call.bridge_id)
            self.ari.request("POST", bridge_path + "/addChannel", channel=caller_id)
            media = self.ari.request(
                "POST",
                "/channels/externalMedia",
                channelId=call.external_id,
                app=self.config.app,
                external_host=self.config.media_host,
                encapsulation="audiosocket",
                transport="tcp",
                connection_type="client",
                format="slin",
                data=call.media_uuid,
            )
            if media and media.get("id") != call.external_id:
                self.external_to_caller.pop(call.external_id, None)
                call.external_id = media["id"]
                self.external_to_caller[call.external_id] = caller_id
            self.ari.request("POST", bridge_path + "/addChannel", channel=call.external_id)
            LOG.info("Bridge %s ready for call %s", call.bridge_id, caller_id)
        except AriError:
            LOG.exception("Failed to connect call %s to AudioSocket", caller_id)
            self.ari.discard("DELETE", caller_path)
            self.end_call(caller_id)

    def on_digit(self, call: Call, digit: str) -> None:
        if digit == "#":
            LOG.info("DTMF # on %s: hangup", call.caller_id)
            self.ari.discard("DELETE", f"/channels/{quote(call.caller_id, safe='')}")
            self.end_call(call.caller_id)
        elif digit == "0" and not call.transferring:
            LOG.info("DTMF 0 on %s: transfer to operator_general", call.caller_id)
            call.transferring = True
            caller_path = f"/channels/{quote(call.caller_id, safe='')}"
            bridge_path = f"/bridges/{quote(call.bridge_id, safe='')}"
            try:
                self.ari.request(
                    "POST", caller_path + "/variable",
                    variable="SAQTA_SUMMARY", value="Smoke test: caller requested an operator with DTMF 0",
                )
                self.ari.request("POST", bridge_path + "/removeChannel", channel=call.caller_id)
                self.end_call(call.caller_id)
                self.ari.request(
                    "POST", caller_path + "/continue",
                    context="saqta-transfer", extension="operator_general", priority="1",
                )
            except AriError:
                LOG.exception("Transfer failed for %s", call.caller_id)
                self.ari.discard("DELETE", caller_path)
                self.end_call(call.caller_id)

    def end_call(self, caller_id: str) -> None:
        call = self.calls.pop(caller_id, None)
        if not call:
            return
        self.external_to_caller.pop(call.external_id, None)
        self.ari.discard("DELETE", f"/channels/{quote(call.external_id, safe='')}")
        self.ari.discard("DELETE", f"/bridges/{quote(call.bridge_id, safe='')}")
        LOG.info("Released call %s", caller_id)

    def close_all(self) -> None:
        for caller_id in list(self.calls):
            self.ari.discard("DELETE", f"/channels/{quote(caller_id, safe='')}")
            self.end_call(caller_id)

    def run(self) -> None:
        try:
            while True:
                try:
                    # ARI authenticates this WebSocket with api_key. Never log its URL.
                    socket = create_connection(self.config.events_url, timeout=2)
                    LOG.info("Connected to ARI app %s", self.config.app)
                    try:
                        while True:
                            try:
                                raw = socket.recv()
                            except WebSocketTimeoutException:
                                continue
                            if not raw:
                                raise WebSocketConnectionClosedException("ARI WebSocket closed")
                            try:
                                self.handle(json.loads(raw))
                            except (ValueError, TypeError):
                                LOG.warning("Ignoring invalid ARI event")
                    finally:
                        socket.close()
                except Exception as exc:
                    LOG.warning("ARI connection lost: %s; reconnecting in 2 seconds", exc)
                    self.close_all()
                    time.sleep(2)
        except KeyboardInterrupt:
            LOG.info("Stopping")
        finally:
            self.close_all()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config.from_env()
    LOG.info("AudioSocket target: %s", config.media_host)
    SmokeController(config).run()


if __name__ == "__main__":
    main()
