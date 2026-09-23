"""Upload finalized MixMonitor WAV files and notify the backend.

Asterisk writes ``<call_id>.wav`` and, only after MixMonitor has stopped,
atomically creates ``<call_id>.ready`` in RECORDINGS_DIR. The marker's mtime
is the call end time. A marker is retained until both S3 upload and backend
notification succeed, so restarting this process safely retries unfinished
work. The backend must deduplicate callbacks by call_id / Idempotency-Key.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import signal
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError


LOG = logging.getLogger("recording-uploader")
STOP = False
POLL_SECONDS = 2
MAX_RETRY_SECONDS = 60
PRESIGNED_URL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class Settings:
    recordings_dir: Path
    minio_endpoint: str
    minio_public_endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    callback_base_url: str
    callback_token: str

    @classmethod
    def from_env(cls) -> Settings:
        values = cls(
            recordings_dir=Path(os.environ.get("RECORDINGS_DIR", "/recordings")),
            minio_endpoint=os.environ.get("MINIO_ENDPOINT", "http://minio:9000").rstrip("/"),
            minio_public_endpoint=os.environ.get(
                "MINIO_PUBLIC_ENDPOINT", "http://127.0.0.1:9000"
            ).rstrip("/"),
            access_key=os.environ.get("MINIO_ACCESS_KEY", ""),
            secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
            bucket=os.environ.get("MINIO_BUCKET", "call-recordings"),
            callback_base_url=os.environ.get(
                "RECORDING_CALLBACK_BASE_URL", "http://mock:8000"
            ).rstrip("/"),
            callback_token=os.environ.get("RECORDING_CALLBACK_TOKEN", ""),
        )
        if not values.access_key or not values.secret_key:
            raise ValueError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY are required")
        if not values.bucket or "/" in values.bucket:
            raise ValueError("MINIO_BUCKET must be a bucket name")
        for name, endpoint in (
            ("MINIO_ENDPOINT", values.minio_endpoint),
            ("MINIO_PUBLIC_ENDPOINT", values.minio_public_endpoint),
            ("RECORDING_CALLBACK_BASE_URL", values.callback_base_url),
        ):
            parsed = urlsplit(endpoint)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError(f"{name} must be an http(s) URL")
        return values


def s3_client(settings: Settings, endpoint: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.access_key,
        aws_secret_access_key=settings.secret_key,
        region_name="us-east-1",
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def is_missing_s3_error(error: ClientError) -> bool:
    code = str(error.response.get("Error", {}).get("Code", ""))
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in ("404", "NoSuchBucket", "NoSuchKey", "NotFound") or status == 404


def ensure_bucket(client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        if not is_missing_s3_error(error):
            raise
        try:
            client.create_bucket(Bucket=bucket)
        except ClientError as create_error:
            # Another uploader may have created it between HEAD and CREATE.
            client.head_bucket(Bucket=bucket)


def call_id_from_marker(marker: Path, suffix: str) -> str | None:
    if marker.suffix != suffix or marker.is_symlink() or not marker.is_file():
        return None
    name = marker.stem
    # Asterisk UNIQUEID is commonly digits-dot, for example 1790163260.1.
    return name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) else None


def wav_metadata(path: Path) -> tuple[int, int, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("WAV is missing or is not a regular file")
    before = path.stat()
    if before.st_size <= 44:
        raise ValueError("WAV is empty")
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            if frames <= 0 or rate <= 0:
                raise ValueError("WAV has no audio frames")
            duration_ms = round(1000 * frames / rate)
    except (wave.Error, EOFError) as error:
        raise ValueError("WAV header is invalid") from error

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("WAV changed while being inspected")
    return after.st_size, duration_ms, digest.hexdigest()


def upload_if_needed(client, settings: Settings, path: Path, key: str, size: int, sha256: str) -> None:
    ensure_bucket(client, settings.bucket)
    try:
        existing = client.head_object(Bucket=settings.bucket, Key=key)
    except ClientError as error:
        if not is_missing_s3_error(error):
            raise
    else:
        if existing["ContentLength"] != size or existing.get("Metadata", {}).get("sha256") != sha256:
            raise ValueError("S3 object already exists with different content")
        LOG.info("Recording object already uploaded: %s", key)
        return

    client.upload_file(
        str(path),
        settings.bucket,
        key,
        ExtraArgs={
            "ContentType": "audio/wav",
            "Metadata": {"sha256": sha256},
        },
    )
    existing = client.head_object(Bucket=settings.bucket, Key=key)
    if existing["ContentLength"] != size or existing.get("Metadata", {}).get("sha256") != sha256:
        raise ValueError("Uploaded S3 object failed integrity check")
    LOG.info("Uploaded recording: %s (%d bytes)", key, size)


def callback(settings: Settings, call_id: str, payload: dict) -> None:
    import json

    url = f"{settings.callback_base_url}/api/telephony/calls/{quote(call_id)}/recording"
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": f"recording:{call_id}",
        },
        method="POST",
    )
    if settings.callback_token:
        request.add_header("Authorization", f"Bearer {settings.callback_token}")
    with urlopen(request, timeout=15) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Backend returned HTTP {response.status}")
    LOG.info("Backend acknowledged recording for call %s", call_id)


def clean_uploaded_marker(marker: Path, call_id: str) -> None:
    """Finish cleanup after a successful callback, including after restart."""
    wav = marker.with_name(f"{call_id}.wav")
    if wav.exists():
        if wav.is_symlink() or not wav.is_file():
            raise ValueError("Cannot clean up a non-regular WAV")
        wav.unlink()
    marker.unlink()
    LOG.info("Cleaned local recording for call %s", call_id)


def process_ready(settings: Settings, client, public_client, marker: Path, call_id: str) -> None:
    wav = marker.with_name(f"{call_id}.wav")
    size, duration_ms, sha256 = wav_metadata(wav)
    ended_at = datetime.fromtimestamp(marker.stat().st_mtime, timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    key = f"calls/{call_id}.wav"
    upload_if_needed(client, settings, wav, key, size, sha256)
    recording_url = public_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.bucket, "Key": key},
        ExpiresIn=PRESIGNED_URL_SECONDS,
        HttpMethod="GET",
    )
    callback(
        settings,
        call_id,
        {
            "call_id": call_id,
            "recording_uri": f"s3://{settings.bucket}/{key}",
            "recording_url": recording_url,
            "bucket": settings.bucket,
            "object_key": key,
            "content_type": "audio/wav",
            "size_bytes": size,
            "duration_ms": duration_ms,
            "ended_at": ended_at,
            "sha256": sha256,
        },
    )
    uploaded = marker.with_suffix(".uploaded")
    if uploaded.exists():
        raise ValueError("Uploaded marker already exists")
    marker.rename(uploaded)
    clean_uploaded_marker(uploaded, call_id)


def stop(_signal_number, _frame) -> None:
    global STOP
    STOP = True


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    client = s3_client(settings, settings.minio_endpoint)
    public_client = s3_client(settings, settings.minio_public_endpoint)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    retry_after: dict[Path, float] = {}
    retry_delay: dict[Path, int] = {}
    LOG.info("Watching %s for finalized WAV recordings", settings.recordings_dir)

    while not STOP:
        for marker in sorted(settings.recordings_dir.glob("*.uploaded")):
            call_id = call_id_from_marker(marker, ".uploaded")
            if call_id is None:
                LOG.warning("Ignoring invalid uploaded marker: %s", marker.name)
                continue
            try:
                clean_uploaded_marker(marker, call_id)
            except OSError as error:
                LOG.warning("Cleanup failed for call %s: %s", call_id, error)

        for marker in sorted(settings.recordings_dir.glob("*.ready")):
            call_id = call_id_from_marker(marker, ".ready")
            if call_id is None:
                LOG.warning("Ignoring invalid ready marker: %s", marker.name)
                continue
            if time.monotonic() < retry_after.get(marker, 0):
                continue
            try:
                process_ready(settings, client, public_client, marker, call_id)
            except HTTPError as error:
                LOG.warning("Backend callback failed for call %s: HTTP %s", call_id, error.code)
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code", "unknown")
                LOG.warning("MinIO operation failed for call %s: %s", call_id, code)
            except ValueError as error:
                LOG.warning("Recording delivery failed for call %s: %s", call_id, error)
            except Exception as error:
                # Never log the callback payload or an arbitrary exception string:
                # either may contain the private signed URL or credentials.
                LOG.warning("Recording delivery failed for call %s: %s", call_id, type(error).__name__)
            else:
                retry_after.pop(marker, None)
                retry_delay.pop(marker, None)
                continue
            delay = min(retry_delay.get(marker, 2) * 2, MAX_RETRY_SECONDS)
            retry_delay[marker] = delay
            retry_after[marker] = time.monotonic() + delay
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
