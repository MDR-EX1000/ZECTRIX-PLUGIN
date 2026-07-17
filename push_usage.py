"""Generate and push the API usage dashboard to a Zectrix device."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError

from usage_image import (
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    UsageImageError,
    generate_usage_image,
)


ZECTRIX_API_BASE_URL = "https://cloud.zectrix.com"
ZECTRIX_API_KEY_FILE = "~/.config/zectrix/api_key"
ZECTRIX_DEVICE_ID_FILE = "~/.config/zectrix/device_id"
MAX_IMAGE_BYTES = 2 * 1024 * 1024


class ZectrixPushError(RuntimeError):
    """Raised when a Zectrix image cannot be validated or pushed."""


def _read_config_file(path: str | Path, label: str) -> str | None:
    config_path = Path(path).expanduser()
    try:
        value = config_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ZectrixPushError(
            f"Cannot read {label} file {config_path}: {exc}"
        ) from exc
    return value or None


def resolve_api_key(api_key_file: str | Path = ZECTRIX_API_KEY_FILE) -> str:
    """Resolve the Zectrix API key without accepting it on the command line."""

    api_key = os.getenv("ZECTRIX_API_KEY", "").strip()
    if not api_key:
        api_key = _read_config_file(api_key_file, "Zectrix API key") or ""
    if not api_key:
        raise ZectrixPushError(
            "Zectrix API key is missing; set ZECTRIX_API_KEY or create "
            f"{Path(api_key_file).expanduser()}"
        )
    return api_key


def _redact(text: str, *secrets: str) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _response_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("msg", "message", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("message")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def _request_json(
    request: Request,
    *,
    timeout: float,
    api_key: str,
) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            payload = None
        message = _response_message(payload)
        detail = f": {message}" if message else ""
        raise ZectrixPushError(
            _redact(
                f"Zectrix request failed with HTTP {exc.code}{detail}",
                api_key,
            )
        ) from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ZectrixPushError(
            _redact(f"Zectrix request failed: {reason}", api_key)
        ) from exc
    except TimeoutError as exc:
        raise ZectrixPushError("Zectrix request timed out") from exc

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ZectrixPushError(
            "Zectrix response is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ZectrixPushError(
            "Zectrix response must be a JSON object"
        )

    code = payload.get("code")
    if code != 0:
        message = _response_message(payload)
        detail = f": {message}" if message else ""
        raise ZectrixPushError(
            _redact(
                f"Zectrix request failed with code {code}{detail}",
                api_key,
            )
        )
    return payload


def _api_url(api_base_url: str, path: str) -> str:
    base = api_base_url.strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("api_base_url must be an absolute HTTP(S) URL")
    return f"{base}{path}"


def list_zectrix_devices(
    api_key: str,
    *,
    api_base_url: str = ZECTRIX_API_BASE_URL,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Return devices available to the configured Open API key."""

    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    request = Request(
        _api_url(api_base_url, "/open/v1/devices"),
        headers={
            "X-API-Key": api_key,
            "Accept": "application/json",
            "User-Agent": "zectrix-api-usage/1.0",
        },
        method="GET",
    )
    payload = _request_json(
        request,
        timeout=timeout,
        api_key=api_key,
    )
    devices = payload.get("data")
    if not isinstance(devices, list):
        raise ZectrixPushError(
            "Zectrix device response is missing data"
        )

    result: list[dict[str, Any]] = []
    for device in devices:
        if isinstance(device, dict):
            result.append(device)
    return result


def resolve_device_id(
    api_key: str,
    *,
    device_id: str | None = None,
    device_id_file: str | Path = ZECTRIX_DEVICE_ID_FILE,
    api_base_url: str = ZECTRIX_API_BASE_URL,
    timeout: float = 10.0,
) -> str:
    """Resolve a configured device or auto-select the only available device."""

    selected = (device_id or os.getenv("ZECTRIX_DEVICE_ID", "")).strip()
    if not selected:
        selected = (
            _read_config_file(device_id_file, "Zectrix device ID") or ""
        )
    if selected:
        return selected

    devices = list_zectrix_devices(
        api_key,
        api_base_url=api_base_url,
        timeout=timeout,
    )
    device_ids = [
        str(device.get("deviceId")).strip()
        for device in devices
        if isinstance(device.get("deviceId"), str)
        and str(device.get("deviceId")).strip()
    ]
    if len(device_ids) == 1:
        return device_ids[0]
    if not device_ids:
        raise ZectrixPushError(
            "No Zectrix devices are available for this API key"
        )
    raise ZectrixPushError(
        "Multiple Zectrix devices are available; set ZECTRIX_DEVICE_ID "
        f"or create {Path(device_id_file).expanduser()}"
    )


def validate_image(image_bytes: bytes) -> None:
    """Validate the Open API file size and the target display dimensions."""

    if not image_bytes:
        raise ZectrixPushError("Image is empty")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ZectrixPushError(
            "Image exceeds the Zectrix 2 MB per-file limit"
        )

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image_format = image.format
            dimensions = image.size
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ZectrixPushError("Image is not a valid image file") from exc

    if image_format != "PNG":
        raise ZectrixPushError("Usage image must be a PNG")
    if dimensions != (IMAGE_WIDTH, IMAGE_HEIGHT):
        raise ZectrixPushError(
            "Usage image must be "
            f"{IMAGE_WIDTH}x{IMAGE_HEIGHT}; got "
            f"{dimensions[0]}x{dimensions[1]}"
        )


def _multipart_body(
    image_bytes: bytes,
    *,
    page_id: str,
    dither: bool,
    boundary: str | None = None,
) -> tuple[bytes, str]:
    separator = boundary or f"----zectrix-{uuid.uuid4().hex}"
    body = bytearray()

    def append_text(value: str) -> None:
        body.extend(value.encode("utf-8"))

    for name, value in (
        ("dither", "true" if dither else "false"),
        ("pageId", page_id),
    ):
        append_text(f"--{separator}\r\n")
        append_text(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        )
        append_text(f"{value}\r\n")

    append_text(f"--{separator}\r\n")
    append_text(
        'Content-Disposition: form-data; name="images"; '
        'filename="api-usage.png"\r\n'
    )
    append_text("Content-Type: image/png\r\n\r\n")
    body.extend(image_bytes)
    append_text("\r\n")
    append_text(f"--{separator}--\r\n")
    return bytes(body), f"multipart/form-data; boundary={separator}"


def push_image(
    api_key: str,
    device_id: str,
    image_bytes: bytes,
    *,
    api_base_url: str = ZECTRIX_API_BASE_URL,
    page_id: str = "1",
    dither: bool = False,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Push one 400x300 PNG to the Zectrix image endpoint."""

    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if page_id not in {"1", "2", "3", "4", "5"}:
        raise ValueError("page_id must be one of: 1, 2, 3, 4, 5")
    if not api_key.strip():
        raise ZectrixPushError("Zectrix API key is empty")
    if not device_id.strip():
        raise ZectrixPushError("Zectrix device ID is empty")

    validate_image(image_bytes)
    body, content_type = _multipart_body(
        image_bytes,
        page_id=page_id,
        dither=dither,
    )
    encoded_device_id = quote(device_id.strip(), safe="")
    request = Request(
        _api_url(
            api_base_url,
            f"/open/v1/devices/{encoded_device_id}/display/image",
        ),
        data=body,
        headers={
            "X-API-Key": api_key,
            "Accept": "application/json",
            "Content-Type": content_type,
            "User-Agent": "zectrix-api-usage/1.0",
        },
        method="POST",
    )
    payload = _request_json(
        request,
        timeout=timeout,
        api_key=api_key,
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ZectrixPushError(
            "Zectrix push response is missing data"
        )
    return data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Push the 400x300 API usage dashboard to Zectrix.",
    )
    parser.add_argument(
        "--image",
        type=Path,
        help="Push an existing 400x300 PNG instead of generating one",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Generated image path "
            "(default: ./api_usage.png; overwritten each run)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and validate the image without contacting Zectrix",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List devices available to the configured API key and exit",
    )
    parser.add_argument(
        "--provider",
        choices=("both", "kimi", "deepseek"),
        default="both",
        help="Usage provider to query when generating (default: both)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Per-request timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv(
            "ZECTRIX_API_BASE_URL",
            ZECTRIX_API_BASE_URL,
        ),
        help="Zectrix Open API base URL",
    )
    parser.add_argument(
        "--api-key-file",
        default=os.getenv(
            "ZECTRIX_API_KEY_FILE",
            ZECTRIX_API_KEY_FILE,
        ),
        help="Fallback Zectrix API key file",
    )
    parser.add_argument(
        "--device-id",
        help="Target device ID; defaults to config or auto-discovery",
    )
    parser.add_argument(
        "--device-id-file",
        default=os.getenv(
            "ZECTRIX_DEVICE_ID_FILE",
            ZECTRIX_DEVICE_ID_FILE,
        ),
        help="Fallback Zectrix device ID file",
    )
    parser.add_argument(
        "--page-id",
        default=os.getenv("ZECTRIX_PAGE_ID", "1"),
        choices=("1", "2", "3", "4", "5"),
        help="Persistent page number (default: 1)",
    )
    parser.add_argument(
        "--dither",
        action="store_true",
        help="Enable server-side dithering (off by default for 1-bit PNG)",
    )
    parser.add_argument(
        "--kimi-api-key-file",
        default=os.getenv(
            "KIMI_API_KEY_FILE",
            "~/.config/zectrix/kimi_api_key",
        ),
        help="Fallback Kimi API key file used during generation",
    )
    return parser


def _print_devices(devices: list[dict[str, Any]]) -> None:
    if not devices:
        print("No devices found.")
        return
    for device in devices:
        device_id = device.get("deviceId", "")
        alias = device.get("alias", "")
        board = device.get("board", "")
        print(f"{device_id}\t{alias}\t{board}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.list_devices:
            api_key = resolve_api_key(args.api_key_file)
            devices = list_zectrix_devices(
                api_key,
                api_base_url=args.api_base_url,
                timeout=args.timeout,
            )
            _print_devices(devices)
            return 0

        if args.image is not None:
            image_bytes = args.image.read_bytes()
            collection = None
            output_path = args.output
        else:
            image_bytes, collection = generate_usage_image(
                args.provider,
                timeout=args.timeout,
                kimi_api_key_file=args.kimi_api_key_file,
            )
            output_path = args.output or Path("api_usage.png")

        validate_image(image_bytes)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(image_bytes)

        if collection is not None:
            for name, message in sorted(collection.errors.items()):
                print(
                    f"warning: {name} unavailable: {message}",
                    file=sys.stderr,
                )

        if args.dry_run:
            location = f" at {output_path}" if output_path else ""
            print(
                f"Dry run complete{location}: "
                f"{IMAGE_WIDTH}x{IMAGE_HEIGHT}, {len(image_bytes)} bytes"
            )
            return 0

        api_key = resolve_api_key(args.api_key_file)
        device_id = resolve_device_id(
            api_key,
            device_id=args.device_id,
            device_id_file=args.device_id_file,
            api_base_url=args.api_base_url,
            timeout=args.timeout,
        )
        result = push_image(
            api_key,
            device_id,
            image_bytes,
            api_base_url=args.api_base_url,
            page_id=args.page_id,
            dither=args.dither,
            timeout=args.timeout,
        )
    except (
        OSError,
        UsageImageError,
        ValueError,
        ZectrixPushError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    pushed_pages = result.get("pushedPages", "?")
    total_pages = result.get("totalPages", "?")
    page_id = result.get("pageId", args.page_id)
    print(
        f"Pushed {pushed_pages}/{total_pages} page(s) "
        f"to {device_id} as page {page_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
