"""Helpers for querying API usage information."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


KIMI_USAGE_URL = "https://api.kimi.com/coding/v1/usages"
DEEPSEEK_USAGE_AMOUNT_URL = (
    "https://platform.deepseek.com/api/v0/usage/by_api_key/amount"
)
DEEPSEEK_USAGE_COST_URL = (
    "https://platform.deepseek.com/api/v0/usage/by_api_key/cost"
)
DEEPSEEK_DASHBOARD_TOKEN_FILE = "~/.config/zectrix/deepseek_dashboard_token"
_FIVE_HOURS_IN_SECONDS = 5 * 60 * 60
_DEEPSEEK_DISPLAY_PLACES = Decimal("0.1")
KIMI_USER_LEVEL_MAP = {
    "LEVEL_BASIC": "Moderato",
}


class KimiUsageError(RuntimeError):
    """Raised when Kimi usage information cannot be retrieved or parsed."""


class DeepSeekUsageError(RuntimeError):
    """Raised when DeepSeek usage information cannot be retrieved or parsed."""


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _used_percent(quota: Any) -> float | None:
    if not isinstance(quota, dict):
        return None

    limit = _as_number(quota.get("limit"))
    if limit is None or limit <= 0:
        return None

    used = _as_number(quota.get("used"))
    if used is None:
        remaining = _as_number(quota.get("remaining"))
        if remaining is None:
            return None
        used = limit - remaining

    percentage = max(0.0, min(100.0, used / limit * 100.0))
    return round(percentage, 2)


def _reset_time(quota: Any) -> str | None:
    if not isinstance(quota, dict):
        return None
    for key in ("resetTime", "reset_time", "resetAt", "reset_at"):
        value = quota.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _reset_in(reset_time: str | None, now: datetime) -> str | None:
    if reset_time is None:
        return None

    normalized = reset_time.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        reset_at = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)

    remaining_seconds = max(
        0.0,
        (reset_at.astimezone(timezone.utc) - now).total_seconds(),
    )
    if remaining_seconds == 0:
        return "0min"

    if remaining_seconds < 60 * 60:
        minutes = max(1, int(remaining_seconds / 60 + 0.5))
        return f"{minutes}min"

    rounded_hours = int(remaining_seconds / (60 * 60) + 0.5)
    if remaining_seconds > 24 * 60 * 60:
        days, hours = divmod(rounded_hours, 24)
        return f"{days}d{hours}h"

    return f"{rounded_hours}h"


def _window_seconds(window: Any) -> float | None:
    if not isinstance(window, dict):
        return None

    duration = _as_number(window.get("duration"))
    if duration is None:
        return None

    unit = str(window.get("timeUnit", window.get("time_unit", ""))).upper()
    if "MINUTE" in unit:
        return duration * 60
    if "HOUR" in unit:
        return duration * 60 * 60
    if "DAY" in unit:
        return duration * 24 * 60 * 60
    if "SECOND" in unit or not unit:
        return duration
    return None


def _five_hour_quota(payload: dict[str, Any]) -> dict[str, Any] | None:
    limits = payload.get("limits")
    if not isinstance(limits, list):
        return None

    for item in limits:
        if not isinstance(item, dict):
            continue
        seconds = _window_seconds(item.get("window"))
        if seconds != _FIVE_HOURS_IN_SECONDS:
            continue
        detail = item.get("detail")
        return detail if isinstance(detail, dict) else item
    return None


def _http_error_message(body: str, status: int) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return f"Kimi usage request failed with HTTP {status}"

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return f"Kimi usage request failed with HTTP {status}: {error['message']}"
        if isinstance(payload.get("message"), str):
            return f"Kimi usage request failed with HTTP {status}: {payload['message']}"
    return f"Kimi usage request failed with HTTP {status}"


def usage_kimi(
    api_key: str | None = None,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Return the current Kimi Coding usage summary.

    Args:
        api_key: Kimi Coding API key. Defaults to ``KIMI_API_KEY``.
        timeout: HTTP request timeout in seconds.

    Returns:
        A dictionary with the following shape. Reset times are UTC ISO 8601
        strings returned by Kimi. ``reset_in`` uses rounded integer minutes
        below one hour, rounded integer hours up to 24 hours, and ``AdBh``
        above 24 hours. ``user_level`` uses the display name from
        ``KIMI_USER_LEVEL_MAP`` and falls back to Kimi's raw membership value
        for unknown levels.

        {
            "5h": {
                "used_percent": 0.0,
                "reset_time": "2026-07-17T11:47:43.520574Z",
                "reset_in": "3h",
            },
            "week": {
                "used_percent": 12.0,
                "reset_time": "2026-07-23T15:47:43.520574Z",
                "reset_in": "6d7h",
            },
            "user_level": "Moderato",
        }

    Raises:
        KimiUsageError: If the API key is missing, the request fails, or the
            response is not a JSON object.
    """

    key = api_key or os.getenv("KIMI_API_KEY")
    if not key:
        raise KimiUsageError(
            "Kimi API key is missing; pass api_key or set KIMI_API_KEY"
        )
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    request = Request(
        KIMI_USAGE_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "zectrix-api-usage/1.0",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise KimiUsageError(_http_error_message(body, exc.code)) from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise KimiUsageError(f"Kimi usage request failed: {reason}") from exc
    except TimeoutError as exc:
        raise KimiUsageError("Kimi usage request timed out") from exc

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise KimiUsageError("Kimi usage response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise KimiUsageError("Kimi usage response must be a JSON object")

    five_hour = _five_hour_quota(payload)
    weekly = payload.get("usage")
    user = payload.get("user")
    membership = user.get("membership") if isinstance(user, dict) else None
    user_level = membership.get("level") if isinstance(membership, dict) else None
    five_hour_reset = _reset_time(five_hour)
    weekly_reset = _reset_time(weekly)
    now = datetime.now(timezone.utc)

    return {
        "5h": {
            "used_percent": _used_percent(five_hour),
            "reset_time": five_hour_reset,
            "reset_in": _reset_in(five_hour_reset, now),
        },
        "week": {
            "used_percent": _used_percent(weekly),
            "reset_time": weekly_reset,
            "reset_in": _reset_in(weekly_reset, now),
        },
        "user_level": (
            KIMI_USER_LEVEL_MAP.get(user_level, user_level)
            if isinstance(user_level, str)
            else None
        ),
    }


def _deepseek_dashboard_token(dashboard_token: str | None) -> str:
    if dashboard_token is not None:
        token = dashboard_token.strip()
        if token:
            return token
        raise DeepSeekUsageError("DeepSeek Dashboard token is empty")

    token = os.getenv("DEEPSEEK_DASHBOARD_TOKEN", "").strip()
    if token:
        return token

    token_file = Path(
        os.getenv(
            "DEEPSEEK_DASHBOARD_TOKEN_FILE",
            DEEPSEEK_DASHBOARD_TOKEN_FILE,
        )
    ).expanduser()
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise DeepSeekUsageError(
            "DeepSeek Dashboard token is missing; pass dashboard_token, "
            "set DEEPSEEK_DASHBOARD_TOKEN, or create "
            f"{token_file}"
        ) from exc
    except OSError as exc:
        raise DeepSeekUsageError(
            f"Cannot read DeepSeek Dashboard token from {token_file}: {exc}"
        ) from exc

    if not token:
        raise DeepSeekUsageError(
            f"DeepSeek Dashboard token file is empty: {token_file}"
        )
    return token


def _deepseek_error_message(body: str, status: int) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return f"DeepSeek usage request failed with HTTP {status}"

    if isinstance(payload, dict):
        message = payload.get("msg", payload.get("message"))
        if isinstance(message, str) and message:
            return (
                f"DeepSeek usage request failed with HTTP {status}: "
                f"{message}"
            )
    return f"DeepSeek usage request failed with HTTP {status}"


def _deepseek_biz_data(
    url: str,
    dashboard_token: str,
    timeout: float,
) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {dashboard_token}",
            "Accept": "application/json",
            "User-Agent": "zectrix-api-usage/1.0",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise DeepSeekUsageError(
            _deepseek_error_message(body, exc.code)
        ) from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise DeepSeekUsageError(
            f"DeepSeek usage request failed: {reason}"
        ) from exc
    except TimeoutError as exc:
        raise DeepSeekUsageError("DeepSeek usage request timed out") from exc

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise DeepSeekUsageError(
            "DeepSeek usage response is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise DeepSeekUsageError(
            "DeepSeek usage response must be a JSON object"
        )

    code = payload.get("code")
    if code != 0:
        message = payload.get("msg")
        detail = (
            f": {message}"
            if isinstance(message, str) and message
            else ""
        )
        raise DeepSeekUsageError(
            f"DeepSeek usage request failed with code {code}{detail}"
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        raise DeepSeekUsageError(
            "DeepSeek usage response is missing data"
        )

    biz_code = data.get("biz_code")
    if biz_code != 0:
        message = data.get("biz_msg")
        detail = (
            f": {message}"
            if isinstance(message, str) and message
            else ""
        )
        raise DeepSeekUsageError(
            f"DeepSeek usage request failed with business code "
            f"{biz_code}{detail}"
        )

    biz_data = data.get("biz_data")
    if not isinstance(biz_data, dict):
        raise DeepSeekUsageError(
            "DeepSeek usage response is missing business data"
        )
    return biz_data


def _deepseek_usage_url(
    base_url: str,
    start: datetime,
    end: datetime,
) -> str:
    query = urlencode(
        {
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
            "tz": 0,
        }
    )
    return f"{base_url}?{query}"


def _deepseek_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise DeepSeekUsageError(
            f"DeepSeek usage field {field} must be an integer"
        )

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DeepSeekUsageError(
            f"DeepSeek usage field {field} must be an integer"
        ) from exc

    if isinstance(value, float) and not value.is_integer():
        raise DeepSeekUsageError(
            f"DeepSeek usage field {field} must be an integer"
        )
    if parsed < 0:
        raise DeepSeekUsageError(
            f"DeepSeek usage field {field} cannot be negative"
        )
    return parsed


def _deepseek_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise DeepSeekUsageError(
            f"DeepSeek usage field {field} must be numeric"
        )
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DeepSeekUsageError(
            f"DeepSeek usage field {field} must be numeric"
        ) from exc
    if parsed < 0:
        raise DeepSeekUsageError(
            f"DeepSeek usage field {field} cannot be negative"
        )
    return parsed


def _deepseek_token_totals(
    payload: dict[str, Any],
    start: datetime,
    end: datetime,
) -> dict[str, int]:
    series = payload.get("series")
    if not isinstance(series, list):
        raise DeepSeekUsageError(
            "DeepSeek amount response is missing series"
        )

    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())
    response_tokens = 0
    cache_hit_tokens = 0
    cache_miss_tokens = 0

    for item in series:
        if not isinstance(item, dict):
            raise DeepSeekUsageError(
                "DeepSeek amount series item must be an object"
            )
        buckets = item.get("buckets")
        if not isinstance(buckets, list):
            raise DeepSeekUsageError(
                "DeepSeek amount series is missing buckets"
            )

        for bucket in buckets:
            if not isinstance(bucket, dict):
                raise DeepSeekUsageError(
                    "DeepSeek amount bucket must be an object"
                )
            bucket_time = _deepseek_int(bucket.get("time"), "time")
            if not start_epoch <= bucket_time < end_epoch:
                continue

            usage = bucket.get("usage")
            if not isinstance(usage, dict):
                raise DeepSeekUsageError(
                    "DeepSeek amount bucket is missing usage"
                )
            response_tokens += _deepseek_int(
                usage.get("RESPONSE_TOKEN"),
                "RESPONSE_TOKEN",
            )
            cache_hit_tokens += _deepseek_int(
                usage.get("PROMPT_CACHE_HIT_TOKEN"),
                "PROMPT_CACHE_HIT_TOKEN",
            )
            cache_miss_tokens += _deepseek_int(
                usage.get("PROMPT_CACHE_MISS_TOKEN"),
                "PROMPT_CACHE_MISS_TOKEN",
            )

    return {
        "response": response_tokens,
        "cache_hit": cache_hit_tokens,
        "cache_miss": cache_miss_tokens,
        "total": (
            response_tokens
            + cache_hit_tokens
            + cache_miss_tokens
        ),
    }


def _deepseek_cost_cny(
    payload: dict[str, Any],
    start: datetime,
    end: datetime,
) -> Decimal:
    currency_groups = payload.get("data")
    if not isinstance(currency_groups, list):
        raise DeepSeekUsageError(
            "DeepSeek cost response is missing data"
        )

    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())
    total = Decimal("0")

    for currency_group in currency_groups:
        if not isinstance(currency_group, dict):
            raise DeepSeekUsageError(
                "DeepSeek cost currency group must be an object"
            )
        if currency_group.get("currency") != "CNY":
            continue
        series = currency_group.get("series")
        if not isinstance(series, list):
            raise DeepSeekUsageError(
                "DeepSeek cost currency group is missing series"
            )

        for item in series:
            if not isinstance(item, dict):
                raise DeepSeekUsageError(
                    "DeepSeek cost series item must be an object"
                )
            buckets = item.get("buckets")
            if not isinstance(buckets, list):
                raise DeepSeekUsageError(
                    "DeepSeek cost series is missing buckets"
                )

            for bucket in buckets:
                if not isinstance(bucket, dict):
                    raise DeepSeekUsageError(
                        "DeepSeek cost bucket must be an object"
                    )
                bucket_time = _deepseek_int(bucket.get("time"), "time")
                if start_epoch <= bucket_time < end_epoch:
                    total += _deepseek_decimal(
                        bucket.get("cost"),
                        "cost",
                    )

    return total


def _rounded_decimal(value: Decimal) -> float:
    return float(
        value.quantize(
            _DEEPSEEK_DISPLAY_PLACES,
            rounding=ROUND_HALF_UP,
        )
    )


def _compact_tokens(tokens: int) -> str:
    units = (
        (1, ""),
        (1_000, "K"),
        (1_000_000, "M"),
        (1_000_000_000, "B"),
    )
    unit_index = 0
    for index, (divisor, _) in enumerate(units):
        if tokens >= divisor:
            unit_index = index

    while True:
        divisor, suffix = units[unit_index]
        compact = (
            Decimal(tokens)
            / Decimal(divisor)
        ).quantize(
            _DEEPSEEK_DISPLAY_PLACES,
            rounding=ROUND_HALF_UP,
        )
        if compact < 1000 or unit_index == len(units) - 1:
            text = format(compact, ".1f")
            return f"{text}{suffix}"
        unit_index += 1


def _cache_hit_percent(hit_tokens: int, miss_tokens: int) -> float | None:
    prompt_tokens = hit_tokens + miss_tokens
    if prompt_tokens == 0:
        return None
    percentage = (
        Decimal(hit_tokens)
        / Decimal(prompt_tokens)
        * Decimal("100")
    )
    return _rounded_decimal(percentage)


def usage_deepseek(
    dashboard_token: str | None = None,
    *,
    timeout: float = 10.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the current DeepSeek Dashboard usage summary.

    DeepSeek's internal usage API requires the Dashboard Bearer token from
    a logged-in platform session; ``DEEPSEEK_API_KEY`` cannot access it.
    The token is resolved from ``dashboard_token``, then
    ``DEEPSEEK_DASHBOARD_TOKEN``, then
    ``~/.config/zectrix/deepseek_dashboard_token``.

    UTC calendar-day boundaries are used because the Dashboard API requires
    day-aligned timestamps. Total Token usage is the sum of response,
    prompt-cache-hit, and prompt-cache-miss Tokens. The three-day Cache hit
    rate is:

        cache-hit / (cache-hit + cache-miss) * 100

    Returns:
        {
            "month": {
                "tokens": "419.8M",
                "cost_cny": 43.9,
            },
            "today": {
                "tokens": "143.1M",
                "cost_cny": 12.4,
            },
            "3d": {
                "cache_hit_percent": 98.7,
            },
        }

    Raises:
        DeepSeekUsageError: If the Dashboard token is missing, a request
            fails, or a response has an unexpected shape.
        ValueError: If timeout is not positive or now is timezone-naive.
    """

    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)

    today_start = current.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    day_end = today_start + timedelta(days=1)
    month_start = today_start.replace(day=1)
    three_day_start = today_start - timedelta(days=2)
    amount_start = min(month_start, three_day_start)
    token = _deepseek_dashboard_token(dashboard_token)

    amount_payload = _deepseek_biz_data(
        _deepseek_usage_url(
            DEEPSEEK_USAGE_AMOUNT_URL,
            amount_start,
            day_end,
        ),
        token,
        timeout,
    )
    cost_payload = _deepseek_biz_data(
        _deepseek_usage_url(
            DEEPSEEK_USAGE_COST_URL,
            month_start,
            day_end,
        ),
        token,
        timeout,
    )

    month_tokens = _deepseek_token_totals(
        amount_payload,
        month_start,
        day_end,
    )
    today_tokens = _deepseek_token_totals(
        amount_payload,
        today_start,
        day_end,
    )
    three_day_tokens = _deepseek_token_totals(
        amount_payload,
        three_day_start,
        day_end,
    )

    return {
        "month": {
            "tokens": _compact_tokens(month_tokens["total"]),
            "cost_cny": _rounded_decimal(
                _deepseek_cost_cny(
                    cost_payload,
                    month_start,
                    day_end,
                )
            ),
        },
        "today": {
            "tokens": _compact_tokens(today_tokens["total"]),
            "cost_cny": _rounded_decimal(
                _deepseek_cost_cny(
                    cost_payload,
                    today_start,
                    day_end,
                )
            ),
        },
        "3d": {
            "cache_hit_percent": _cache_hit_percent(
                three_day_tokens["cache_hit"],
                three_day_tokens["cache_miss"],
            ),
        },
    }
