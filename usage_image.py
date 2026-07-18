"""Render Kimi and DeepSeek usage as an 800x600 grayscale dashboard."""

from __future__ import annotations

import argparse
import io
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from PIL import Image, ImageDraw, ImageFont

from api_usage import (
    DeepSeekUsageError,
    KimiUsageError,
    usage_deepseek,
    usage_kimi,
)


# The layout remains authored on a 400x300 logical grid and is exported at
# twice that size for the adopted 800x600 device image.
IMAGE_WIDTH = 800
IMAGE_HEIGHT = 600
_LAYOUT_SCALE = 2
_LOGICAL_WIDTH = IMAGE_WIDTH // _LAYOUT_SCALE
_LOGICAL_HEIGHT = IMAGE_HEIGHT // _LAYOUT_SCALE
_BANNER_HEIGHT = 32
_SLOGAN_SIZE = 11
_SLOGAN_MINIMUM_SIZE = 10
_KIMI_PROGRESS_TOP = 78
_SECTION_DIVIDER_Y = 158
_DEEPSEEK_TOP = 178
_DEEPSEEK_METRIC_TOP = 202
_DEEPSEEK_DETAIL_OFFSET = 65
_DEEPSEEK_LINE_BOTTOM = 282
_DEEPSEEK_VALUE_SIZE = 20
_DEEPSEEK_VALUE_MINIMUM_SIZE = 14
_RENDER_SCALE = 4
_ONE_DECIMAL = Decimal("0.1")
_WHOLE_NUMBER = Decimal("1")
_DISPLAY_TIMEZONE = timezone(timedelta(hours=8))
KIMI_API_KEY_FILE = "~/.config/zectrix/kimi_api_key"
_FONT_ASSET_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
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
ENGLISH_SLOGANS = (
    "IT WORKS ON MY MACHINE",
    "LGTM. SHIP IT.",
    "PROMPT GOES BRRR",
    "NO TESTS, ONLY VIBES",
    "ONE MORE PROMPT",
    "404: SLEEP NOT FOUND",
    "MAY THE CODE BE WITH YOU",
)
CHINESE_SLOGANS = (
    "上下文已加载",
    "人类负责想，AI 负责敲",
    "先跑起来再说",
    "再重构最后一次",
    "这次一定不改需求",
    "缓存命中，心情稳定",
    "最后再改亿点点",
)
DAILY_SLOGANS = tuple(
    slogan
    for pair in zip(ENGLISH_SLOGANS, CHINESE_SLOGANS)
    for slogan in pair
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
_FONT_MONO_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
)
_FONT_SLOGAN = (
    str(_FONT_ASSET_DIR / "msyhbd.ttc"),
    str(_FONT_ASSET_DIR / "msyh.ttc"),
    str(_FONT_ASSET_DIR / "NotoSansSC-Slogan-Bold.otf"),
)


class UsageImageError(RuntimeError):
    """Raised when usage data cannot be rendered."""


@dataclass(frozen=True)
class RenderContext:
    """Immutable input passed to a registered visual design."""

    kimi: Mapping[str, Any] | None
    deepseek: Mapping[str, Any] | None
    updated_at: datetime
    slogan: str
    width: int = IMAGE_WIDTH
    height: int = IMAGE_HEIGHT


class UsageDesign(Protocol):
    """Protocol implemented by one dashboard visual design."""

    name: str
    description: str

    def render(self, context: RenderContext) -> bytes:
        """Render one complete PNG from a context."""


@dataclass(frozen=True)
class _FunctionDesign:
    name: str
    description: str
    renderer: Callable[[RenderContext], bytes]

    def render(self, context: RenderContext) -> bytes:
        return self.renderer(context)


_DESIGNS: dict[str, UsageDesign] = {}


def register_design(
    name: str,
    renderer: Callable[[RenderContext], bytes],
    *,
    description: str = "",
) -> None:
    """Register a named visual design.

    Registration is intentionally small and explicit: adding a new design
    does not require changing the data collection or push layers.
    """

    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("design name must not be empty")
    if not callable(renderer):
        raise TypeError("design renderer must be callable")
    if normalized == "rotate":
        raise ValueError("'rotate' is reserved for automatic selection")
    if normalized in _DESIGNS:
        raise ValueError(f"design already registered: {normalized}")
    _DESIGNS[normalized] = _FunctionDesign(
        name=normalized,
        description=description.strip(),
        renderer=renderer,
    )


def list_designs() -> tuple[UsageDesign, ...]:
    """Return registered designs in deterministic rotation order."""

    return tuple(_DESIGNS.values())


def _design_day_number(moment: datetime) -> int:
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    return moment.astimezone(_DISPLAY_TIMEZONE).date().toordinal()


def design_index_for_day(moment: datetime, design_count: int) -> int:
    """Return the phase-shifted design index for a UTC+8 calendar day.

    The slogan cycle has 14 slots. Shifting the design phase once per slogan
    cycle visits every slogan/design pair for any number of registered
    designs, including cases where the design count shares a factor with 14.
    """

    if design_count <= 0:
        raise ValueError("design_count must be greater than zero")
    day_number = _design_day_number(moment)
    slogan_slot = day_number % len(DAILY_SLOGANS)
    slogan_cycle = day_number // len(DAILY_SLOGANS)
    return (slogan_slot + slogan_cycle) % design_count


def resolve_design_name(
    design: str,
    moment: datetime,
    *,
    designs: Sequence[UsageDesign] | None = None,
) -> str:
    """Resolve an explicit design name or the automatic daily rotation."""

    normalized = design.strip().lower()
    available = tuple(designs) if designs is not None else list_designs()
    if not available:
        raise UsageImageError("No usage image designs are registered")
    normalized_names: list[str] = []
    for item in available:
        item_name = item.name.strip().lower()
        if not item_name:
            raise ValueError("registered design name must not be empty")
        if item_name in normalized_names:
            raise ValueError(f"duplicate design name: {item_name}")
        normalized_names.append(item_name)
    by_name = dict(zip(normalized_names, available))
    if normalized == "rotate":
        return normalized_names[
            design_index_for_day(moment, len(available))
        ]
    if normalized not in by_name:
        available_names = ", ".join(by_name) or "none"
        raise ValueError(
            f"unknown design {design!r}; available designs: {available_names}"
        )
    return normalized


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
    configured_slogan_font = os.getenv("SLOGAN_FONT_FILE", "").strip()
    candidates = {
        "regular": _FONT_REGULAR,
        "semibold": _FONT_SEMIBOLD,
        "black": _FONT_BLACK,
        "mono": _FONT_MONO,
        "mono-regular": _FONT_MONO_REGULAR,
        "slogan": (
            *((configured_slogan_font,) if configured_slogan_font else ()),
            *_FONT_SLOGAN,
        ),
    }[weight]

    for path in candidates:
        try:
            return ImageFont.truetype(path, max(1, size))
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


def _whole_number(value: Any, suffix: str = "") -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    rounded = number.quantize(_WHOLE_NUMBER, rounding=ROUND_HALF_UP)
    return f"{format(rounded, '.0f')}{suffix}"


def daily_slogan(moment: datetime) -> str:
    """Return alternating English and Chinese slogans by UTC+8 day."""

    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    display_day = moment.astimezone(_DISPLAY_TIMEZONE).date()
    ordinal = display_day.toordinal()
    pool = ENGLISH_SLOGANS if ordinal % 2 == 0 else CHINESE_SLOGANS
    return pool[(ordinal // 2) % len(pool)]


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
    return round(float(value) * _RENDER_SCALE * _LAYOUT_SCALE)


class _Canvas:
    """Keep shapes supersampled while replaying text at final pixel size."""

    def __init__(self, width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT):
        self.image = Image.new(
            "L",
            (width * _RENDER_SCALE, height * _RENDER_SCALE),
            color=255,
        )
        self.draw = ImageDraw.Draw(self.image)
        self._text_ops: list[
            tuple[tuple[int, int], str, int, str, int, str | None]
        ] = []

    def text(
        self,
        position: tuple[float, float],
        text: str,
        *,
        size: int,
        weight: str = "regular",
        fill: int = 0,
        anchor: str | None = None,
    ) -> None:
        self._text_ops.append(
            (
                (
                    round(position[0] * _LAYOUT_SCALE),
                    round(position[1] * _LAYOUT_SCALE),
                ),
                text,
                max(1, round(size * _LAYOUT_SCALE)),
                weight,
                fill,
                anchor,
            )
        )

    def text_width(self, text: str, size: int, weight: str) -> float:
        return self.draw.textlength(text, font=_font(size, weight=weight))

    def line(self, *args: Any, **kwargs: Any) -> None:
        self.draw.line(*args, **kwargs)

    def rectangle(self, *args: Any, **kwargs: Any) -> None:
        self.draw.rectangle(*args, **kwargs)

    def finalize(self) -> Image.Image:
        flattened = self.image.resize(
            (IMAGE_WIDTH, IMAGE_HEIGHT),
            Image.Resampling.LANCZOS,
        )
        text_draw = ImageDraw.Draw(flattened)
        for position, text, size, weight, fill, anchor in self._text_ops:
            text_draw.text(
                position,
                text,
                font=_font(size, weight=weight),
                fill=fill,
                anchor=anchor,
            )
        return flattened


def _draw_text(
    draw: _Canvas,
    position: tuple[float, float],
    text: str,
    *,
    size: int,
    weight: str = "regular",
    fill: int = 0,
    anchor: str | None = None,
) -> None:
    draw.text(
        position,
        text,
        size=size,
        weight=weight,
        fill=fill,
        anchor=anchor,
    )


def _fit_text(
    draw: _Canvas,
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
        if draw.text_width(text, candidate_size, weight) <= max_width:
            draw.text(
                position,
                text,
                size=candidate_size,
                weight=weight,
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


def _draw_chip(
    draw: _Canvas,
    *,
    text: str,
    left: float,
    middle: float,
    size: int = 8,
    pad_x: int = 5,
    pad_y: int = 2,
) -> None:
    """Draw a compact white-on-black metadata chip."""

    text_width = draw.text_width(text, size, "mono-regular")
    top = middle - size / 2 - pad_y
    bottom = middle + size / 2 + pad_y
    right = left + text_width + 2 * pad_x
    draw.rectangle(
        (_s(left), _s(top), _s(right), _s(bottom)),
        fill=0,
    )
    draw.text(
        (left + pad_x, middle),
        text,
        size=size,
        weight="mono-regular",
        fill=255,
        anchor="lm",
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
    percent_text = _whole_number(percent, "%")

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
        size=22,
        weight="mono",
    )

    reset_text = (
        str(reset_in).strip().lower()
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
        weight="mono-regular",
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
        str(plan).strip().upper()
        if isinstance(plan, str) and plan.strip()
        else "UNKNOWN"
    )
    title_width = draw.text_width("KIMI", 14, "black")
    _draw_chip(
        draw,
        text=plan_text,
        left=14 + title_width + 7,
        middle=60,
    )

    _draw_text(
        draw,
        (386, 57),
        "RATE LIMIT",
        size=8,
        weight="regular",
        anchor="ra",
    )

    _draw_progress(
        draw,
        left=14,
        right=190,
        top=_KIMI_PROGRESS_TOP,
        label="5 HOUR",
        percent=_nested(usage, "5h", "used_percent"),
        reset_in=_nested(usage, "5h", "reset_in"),
    )
    _draw_progress(
        draw,
        left=210,
        right=386,
        top=_KIMI_PROGRESS_TOP,
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
        size=_DEEPSEEK_VALUE_SIZE,
        minimum_size=_DEEPSEEK_VALUE_MINIMUM_SIZE,
        weight="mono",
    )
    _draw_text(
        draw,
        (left, top + _DEEPSEEK_DETAIL_OFFSET),
        detail,
        size=10,
        weight="mono-regular",
    )


def _draw_deepseek(
    draw: ImageDraw.ImageDraw,
    usage: Mapping[str, Any] | None,
) -> None:
    title_width = draw.text_width("DEEPSEEK", 14, "black")
    _draw_text(
        draw,
        (14, _DEEPSEEK_TOP),
        "DEEPSEEK",
        size=14,
        weight="black",
    )
    _draw_chip(
        draw,
        text="API",
        left=14 + title_width + 7,
        middle=_DEEPSEEK_TOP + 6,
    )
    _draw_text(
        draw,
        (386, _DEEPSEEK_TOP + 3),
        "TOKENS / SPEND",
        size=8,
        weight="regular",
        anchor="ra",
    )

    if not isinstance(usage, Mapping):
        _draw_text(
            draw,
            (200, _DEEPSEEK_METRIC_TOP + 35),
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
        top=_DEEPSEEK_METRIC_TOP,
        label="THIS MONTH",
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
        (
            _s(137),
            _s(_DEEPSEEK_METRIC_TOP),
            _s(137),
            _s(_DEEPSEEK_LINE_BOTTOM),
        ),
        fill=0,
        width=_s(1),
    )
    _draw_deepseek_metric(
        draw,
        left=151,
        right=264,
        top=_DEEPSEEK_METRIC_TOP,
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
        (
            _s(273),
            _s(_DEEPSEEK_METRIC_TOP),
            _s(273),
            _s(_DEEPSEEK_LINE_BOTTOM),
        ),
        fill=0,
        width=_s(1),
    )
    _draw_deepseek_metric(
        draw,
        left=287,
        right=386,
        top=_DEEPSEEK_METRIC_TOP,
        label="CACHE HIT",
        value=_one_decimal(
            _nested(usage, "3d", "cache_hit_percent"),
            "%",
        ),
        detail="LAST 3 DAYS",
    )


def _render_daily_grid(context: RenderContext) -> bytes:
    """Render the current black-and-white grid design."""

    current = context.updated_at
    draw = _Canvas(context.width, context.height)

    draw.rectangle(
        (0, 0, _s(_LOGICAL_WIDTH), _s(_BANNER_HEIGHT)),
        fill=0,
    )
    _fit_text(
        draw,
        (14, _BANNER_HEIGHT / 2),
        context.slogan,
        max_width=282,
        size=_SLOGAN_SIZE,
        minimum_size=_SLOGAN_MINIMUM_SIZE,
        weight="slogan",
        fill=255,
        anchor="lm",
    )
    timestamp = (
        f"{current.day:02d} {_MONTH_NAMES[current.month - 1]} "
        f"{current.hour:02d}:{current.minute:02d}"
    )
    _draw_text(
        draw,
        (386, _BANNER_HEIGHT / 2),
        timestamp,
        size=9,
        weight="mono",
        fill=255,
        anchor="rm",
    )

    draw.line(
        (_s(14), _s(_SECTION_DIVIDER_Y), _s(386), _s(_SECTION_DIVIDER_Y)),
        fill=0,
        width=_s(2),
    )
    _draw_kimi(draw, context.kimi)
    _draw_deepseek(draw, context.deepseek)

    flattened = draw.finalize()
    output = io.BytesIO()
    flattened.save(output, format="PNG", optimize=True)
    return output.getvalue()


register_design(
    "daily-grid",
    _render_daily_grid,
    description="Current banner, Kimi quota, and DeepSeek metrics layout",
)


def render_usage_image(
    kimi_usage: Mapping[str, Any] | None,
    deepseek_usage: Mapping[str, Any] | None,
    *,
    updated_at: datetime | None = None,
    design: str = "rotate",
) -> bytes:
    """Return a deterministic 800x600 grayscale PNG.

    ``design='rotate'`` selects a design using the UTC+8 day and the
    phase-shifted 14-slogan scheduler. Passing a registered name pins the
    output to that design.
    """

    current = updated_at or datetime.now(_DISPLAY_TIMEZONE)
    if current.tzinfo is None:
        raise ValueError("updated_at must be timezone-aware")
    current = current.astimezone(_DISPLAY_TIMEZONE)
    selected_name = resolve_design_name(design, current)
    selected = _DESIGNS[selected_name]
    context = RenderContext(
        kimi=kimi_usage,
        deepseek=deepseek_usage,
        updated_at=current,
        slogan=daily_slogan(current),
    )
    return selected.render(context)


def generate_usage_image(
    provider: str = "both",
    *,
    timeout: float = 10.0,
    kimi_api_key: str | None = None,
    kimi_api_key_file: str | Path = KIMI_API_KEY_FILE,
    deepseek_dashboard_token: str | None = None,
    updated_at: datetime | None = None,
    design: str = "rotate",
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
        design=design,
    )
    return image, collection


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render Kimi and DeepSeek usage as an 800x600 PNG.",
    )
    parser.add_argument(
        "--design",
        default=os.getenv("USAGE_IMAGE_DESIGN", "rotate"),
        help=(
            "Visual design name, or rotate for automatic daily rotation "
            "(default: rotate)"
        ),
    )
    parser.add_argument(
        "--list-designs",
        action="store_true",
        help="List registered visual designs and exit",
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
    if args.list_designs:
        for design in list_designs():
            description = f"\t{design.description}" if design.description else ""
            print(f"{design.name}{description}")
        return 0
    try:
        image, collection = generate_usage_image(
            args.provider,
            timeout=args.timeout,
            kimi_api_key_file=args.kimi_api_key_file,
            design=args.design,
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
