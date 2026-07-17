"""Render Kimi and DeepSeek usage as a 400x300 monochrome dashboard."""

from __future__ import annotations

import argparse
import io
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont

from api_usage import (
    DeepSeekUsageError,
    KimiUsageError,
    usage_deepseek,
    usage_kimi,
)


IMAGE_WIDTH = 400
IMAGE_HEIGHT = 300
_RENDER_SCALE = 4
_ONE_DECIMAL = Decimal("0.1")
KIMI_API_KEY_FILE = "~/.config/zectrix/kimi_api_key"
_MONTH_NAMES = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)

_FONT_REGULAR = (
    "/usr/share/fonts/truetype/lato/Lato-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_FONT_SEMIBOLD = (
    "/usr/share/fonts/truetype/lato/Lato-Semibold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
_FONT_BLACK = (
    "/usr/share/fonts/truetype/lato/Lato-Black.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
_FONT_MONO = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
)


class UsageImageError(RuntimeError):
    """Raised when usage data cannot be rendered."""


@dataclass
class UsageCollection:
    """Usage values collected from the provider-specific helpers."""

    kimi: dict[str, Any] | None = None
    deepseek: dict[str, Any] | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return self.kimi is not None or self.deepseek is not None


def _read_optional_secret_file(path: str | Path) -> str | None:
    secret_path = Path(path).expanduser()
    try:
        value = secret_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UsageImageError(
            f"Cannot read credential file {secret_path}: {exc}"
        ) from exc
    return value or None


def collect_usage(
    provider: str = "both",
    *,
    timeout: float = 10.0,
    kimi_api_key: str | None = None,
    kimi_api_key_file: str | Path = KIMI_API_KEY_FILE,
    deepseek_dashboard_token: str | None = None,
) -> UsageCollection:
    """Collect current usage while allowing one provider to fail."""

    if provider not in {"both", "kimi", "deepseek"}:
        raise ValueError("provider must be one of: both, kimi, deepseek")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    result = UsageCollection()

    if provider in {"both", "kimi"}:
        key = kimi_api_key or os.getenv("KIMI_API_KEY")
        if not key:
            key = _read_optional_secret_file(kimi_api_key_file)
        try:
            result.kimi = usage_kimi(api_key=key, timeout=timeout)
        except (KimiUsageError, ValueError) as exc:
            result.errors["kimi"] = str(exc)

    if provider in {"both", "deepseek"}:
        try:
            result.deepseek = usage_deepseek(
                dashboard_token=deepseek_dashboard_token,
                timeout=timeout,
            )
        except (DeepSeekUsageError, ValueError) as exc:
            result.errors["deepseek"] = str(exc)

    return result


def _font(
    size: int,
    *,
    weight: str = "regular",
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = {
        "regular": _FONT_REGULAR,
        "semibold": _FONT_SEMIBOLD,
        "black": _FONT_BLACK,
        "mono": _FONT_MONO,
    }[weight]

    scaled_size = max(1, size * _RENDER_SCALE)
    for path in candidates:
        try:
            return ImageFont.truetype(path, scaled_size)
        except OSError:
            continue
    return ImageFont.load_default()


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _one_decimal(value: Any, suffix: str = "") -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    rounded = number.quantize(_ONE_DECIMAL, rounding=ROUND_HALF_UP)
    return f"{format(rounded, '.1f')}{suffix}"


def _nested(
    payload: Mapping[str, Any] | None,
    section: str,
    field_name: str,
) -> Any:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get(section)
    if not isinstance(value, Mapping):
        return None
    return value.get(field_name)


def _s(value: float | int) -> int:
    return round(float(value) * _RENDER_SCALE)


def _draw_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    *,
    size: int,
    weight: str = "regular",
    fill: int = 0,
    anchor: str | None = None,
) -> None:
    draw.text(
        (_s(position[0]), _s(position[1])),
        text,
        font=_font(size, weight=weight),
        fill=fill,
        anchor=anchor,
    )


def _fit_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    *,
    max_width: int,
    size: int,
    minimum_size: int,
    weight: str,
    fill: int = 0,
    anchor: str | None = None,
) -> None:
    for candidate_size in range(size, minimum_size - 1, -1):
        font = _font(candidate_size, weight=weight)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= _s(max_width):
            draw.text(
                (_s(position[0]), _s(position[1])),
                text,
                font=font,
                fill=fill,
                anchor=anchor,
            )
            return

    _draw_text(
        draw,
        position,
        text,
        size=minimum_size,
        weight=weight,
        fill=fill,
        anchor=anchor,
    )


def _draw_progress(
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    right: int,
    top: int,
    label: str,
    percent: Any,
    reset_in: Any,
) -> None:
    value = _number(percent)
    percent_text = _one_decimal(percent, "%")

    _draw_text(
        draw,
        (left, top),
        label,
        size=9,
        weight="semibold",
    )
    _draw_text(
        draw,
        (left, top + 14),
        percent_text,
        size=24,
        weight="mono",
    )

    reset_text = (
        str(reset_in).strip().upper()
        if isinstance(reset_in, str) and reset_in.strip()
        else "N/A"
    )
    _fit_text(
        draw,
        (right, top + 28),
        f"RESET {reset_text}",
        max_width=82,
        size=9,
        minimum_size=7,
        weight="mono",
        anchor="ra",
    )

    bar_top = top + 51
    bar_bottom = bar_top + 8
    bar_middle = bar_top + 4
    draw.line(
        (_s(left), _s(bar_middle), _s(right), _s(bar_middle)),
        fill=0,
        width=_s(1),
    )
    if value is not None:
        bounded = max(Decimal("0"), min(Decimal("100"), value))
        fill_width = round(
            float(bounded / Decimal("100")) * (right - left)
        )
        if fill_width > 0:
            draw.rectangle(
                (
                    _s(left),
                    _s(bar_top),
                    _s(left + fill_width),
                    _s(bar_bottom),
                ),
                fill=0,
            )


def _draw_kimi(
    draw: ImageDraw.ImageDraw,
    usage: Mapping[str, Any] | None,
) -> None:
    _draw_text(
        draw,
        (14, 54),
        "KIMI",
        size=14,
        weight="black",
    )

    if not isinstance(usage, Mapping):
        _draw_text(
            draw,
            (200, 105),
            "UNAVAILABLE",
            size=13,
            weight="black",
            anchor="mm",
        )
        return

    plan = usage.get("user_level")
    plan_text = (
        str(plan).upper()
        if isinstance(plan, str) and plan.strip()
        else "UNKNOWN"
    )
    _draw_text(
        draw,
        (305, 57),
        "PLAN",
        size=8,
        weight="semibold",
    )
    _fit_text(
        draw,
        (386, 54),
        plan_text,
        max_width=75,
        size=11,
        minimum_size=8,
        weight="mono",
        anchor="ra",
    )

    _draw_progress(
        draw,
        left=14,
        right=190,
        top=79,
        label="5 HOUR",
        percent=_nested(usage, "5h", "used_percent"),
        reset_in=_nested(usage, "5h", "reset_in"),
    )
    _draw_progress(
        draw,
        left=210,
        right=386,
        top=79,
        label="WEEK",
        percent=_nested(usage, "week", "used_percent"),
        reset_in=_nested(usage, "week", "reset_in"),
    )


def _draw_deepseek_metric(
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    right: int,
    top: int,
    label: str,
    value: str,
    detail: str,
) -> None:
    _draw_text(
        draw,
        (left, top),
        label,
        size=9,
        weight="semibold",
    )
    _fit_text(
        draw,
        (left, top + 20),
        value,
        max_width=right - left,
        size=26,
        minimum_size=17,
        weight="mono",
    )
    _draw_text(
        draw,
        (left, top + 59),
        detail,
        size=10,
        weight="mono",
    )


def _draw_deepseek(
    draw: ImageDraw.ImageDraw,
    usage: Mapping[str, Any] | None,
) -> None:
    _draw_text(
        draw,
        (14, 164),
        "DEEPSEEK",
        size=14,
        weight="black",
    )
    _draw_text(
        draw,
        (386, 167),
        "TOKENS / SPEND",
        size=8,
        weight="semibold",
        anchor="ra",
    )

    if not isinstance(usage, Mapping):
        _draw_text(
            draw,
            (200, 229),
            "UNAVAILABLE",
            size=13,
            weight="black",
            anchor="mm",
        )
        return

    _draw_deepseek_metric(
        draw,
        left=14,
        right=128,
        top=194,
        label="MONTH",
        value=(
            str(_nested(usage, "month", "tokens")).upper()
            if _nested(usage, "month", "tokens") is not None
            else "N/A"
        ),
        detail=(
            f"CNY {_one_decimal(_nested(usage, 'month', 'cost_cny'))}"
        ),
    )
    draw.line(
        (_s(137), _s(194), _s(137), _s(276)),
        fill=0,
        width=_s(1),
    )
    _draw_deepseek_metric(
        draw,
        left=151,
        right=264,
        top=194,
        label="TODAY",
        value=(
            str(_nested(usage, "today", "tokens")).upper()
            if _nested(usage, "today", "tokens") is not None
            else "N/A"
        ),
        detail=(
            f"CNY {_one_decimal(_nested(usage, 'today', 'cost_cny'))}"
        ),
    )
    draw.line(
        (_s(273), _s(194), _s(273), _s(276)),
        fill=0,
        width=_s(1),
    )
    _draw_deepseek_metric(
        draw,
        left=287,
        right=386,
        top=194,
        label="3D CACHE HIT",
        value=_one_decimal(
            _nested(usage, "3d", "cache_hit_percent"),
            "%",
        ),
        detail="HIT RATE",
    )


def render_usage_image(
    kimi_usage: Mapping[str, Any] | None,
    deepseek_usage: Mapping[str, Any] | None,
    *,
    updated_at: datetime | None = None,
) -> bytes:
    """Return a deterministic 400x300 monochrome PNG."""

    current = updated_at or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("updated_at must be timezone-aware")
    current = current.astimezone(timezone.utc)

    canvas = Image.new(
        "L",
        (
            IMAGE_WIDTH * _RENDER_SCALE,
            IMAGE_HEIGHT * _RENDER_SCALE,
        ),
        color=255,
    )
    draw = ImageDraw.Draw(canvas)

    draw.rectangle(
        (_s(14), _s(12), _s(18), _s(34)),
        fill=0,
    )
    _draw_text(
        draw,
        (27, 10),
        "API USAGE",
        size=18,
        weight="black",
    )
    timestamp = (
        f"{current.day:02d} {_MONTH_NAMES[current.month - 1]} "
        f"{current.hour:02d}:{current.minute:02d}Z"
    )
    _draw_text(
        draw,
        (386, 21),
        timestamp,
        size=9,
        weight="mono",
        anchor="rm",
    )

    draw.line(
        (_s(14), _s(43), _s(386), _s(43)),
        fill=0,
        width=_s(1),
    )
    draw.line(
        (_s(14), _s(151), _s(386), _s(151)),
        fill=0,
        width=_s(2),
    )
    _draw_kimi(draw, kimi_usage)
    _draw_deepseek(draw, deepseek_usage)

    resized = canvas.resize(
        (IMAGE_WIDTH, IMAGE_HEIGHT),
        Image.Resampling.LANCZOS,
    )
    monochrome = resized.point(
        lambda pixel: 255 if pixel >= 168 else 0,
        mode="1",
    )

    output = io.BytesIO()
    monochrome.save(output, format="PNG", optimize=True)
    return output.getvalue()


def generate_usage_image(
    provider: str = "both",
    *,
    timeout: float = 10.0,
    kimi_api_key: str | None = None,
    kimi_api_key_file: str | Path = KIMI_API_KEY_FILE,
    deepseek_dashboard_token: str | None = None,
    updated_at: datetime | None = None,
) -> tuple[bytes, UsageCollection]:
    """Collect current usage and return its rendered PNG and source status."""

    collection = collect_usage(
        provider,
        timeout=timeout,
        kimi_api_key=kimi_api_key,
        kimi_api_key_file=kimi_api_key_file,
        deepseek_dashboard_token=deepseek_dashboard_token,
    )
    if not collection.has_data:
        details = "; ".join(
            f"{name}: {message}"
            for name, message in sorted(collection.errors.items())
        )
        raise UsageImageError(
            f"No usage provider returned data{': ' + details if details else ''}"
        )

    image = render_usage_image(
        collection.kimi,
        collection.deepseek,
        updated_at=updated_at,
    )
    return image, collection


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render Kimi and DeepSeek usage as a 400x300 PNG.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("api_usage.png"),
        help="PNG output path (default: api_usage.png)",
    )
    parser.add_argument(
        "--provider",
        choices=("both", "kimi", "deepseek"),
        default="both",
        help="Usage provider to query (default: both)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--kimi-api-key-file",
        default=os.getenv(
            "KIMI_API_KEY_FILE",
            KIMI_API_KEY_FILE,
        ),
        help="Fallback Kimi API key file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        image, collection = generate_usage_image(
            args.provider,
            timeout=args.timeout,
            kimi_api_key_file=args.kimi_api_key_file,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(image)
    except (OSError, UsageImageError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for name, message in sorted(collection.errors.items()):
        print(f"warning: {name} unavailable: {message}", file=sys.stderr)
    print(
        f"Rendered {args.output} "
        f"({IMAGE_WIDTH}x{IMAGE_HEIGHT}, {len(image)} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
