import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import api_usage


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


def _success_payload(biz_data):
    return {
        "code": 0,
        "msg": "",
        "data": {
            "biz_code": 0,
            "biz_msg": "",
            "biz_data": biz_data,
        },
    }


class CompactTokensTests(unittest.TestCase):
    def test_formats_and_promotes_units(self):
        cases = {
            0: "0.0",
            999: "999.0",
            1_000: "1.0K",
            1_250: "1.3K",
            999_999: "1.0M",
            1_234_567: "1.2M",
            999_999_999: "1.0B",
            1_099_602_059: "1.1B",
        }

        for tokens, expected in cases.items():
            with self.subTest(tokens=tokens):
                self.assertEqual(api_usage._compact_tokens(tokens), expected)


class KimiUsageTests(unittest.TestCase):
    def test_maps_intermediate_level_to_allegretto(self):
        payload = {
            "limits": [],
            "usage": {},
            "user": {
                "membership": {
                    "level": "LEVEL_INTERMEDIATE",
                }
            },
        }

        with patch(
            "api_usage.urlopen",
            return_value=_FakeResponse(payload),
        ):
            result = api_usage.usage_kimi(api_key="kimi-key")

        self.assertEqual(result["user_level"], "Allegretto")


class DeepSeekUsageTests(unittest.TestCase):
    def test_aggregates_requested_metrics(self):
        jul_1 = 1_782_864_000
        jul_15 = 1_784_073_600
        jul_16 = 1_784_160_000
        jul_17 = 1_784_246_400
        jul_18 = 1_784_332_800
        amount_payload = _success_payload(
            {
                "series": [
                    {
                        "buckets": [
                            {
                                "time": jul_1,
                                "usage": {
                                    "RESPONSE_TOKEN": 100,
                                    "PROMPT_CACHE_HIT_TOKEN": 200,
                                    "PROMPT_CACHE_MISS_TOKEN": 300,
                                },
                            },
                            {
                                "time": jul_15,
                                "usage": {
                                    "RESPONSE_TOKEN": 10,
                                    "PROMPT_CACHE_HIT_TOKEN": 90,
                                    "PROMPT_CACHE_MISS_TOKEN": 10,
                                },
                            },
                            {
                                "time": jul_16,
                                "usage": {
                                    "RESPONSE_TOKEN": 20,
                                    "PROMPT_CACHE_HIT_TOKEN": 80,
                                    "PROMPT_CACHE_MISS_TOKEN": 20,
                                },
                            },
                            {
                                "time": jul_17,
                                "usage": {
                                    "RESPONSE_TOKEN": 30,
                                    "PROMPT_CACHE_HIT_TOKEN": 70,
                                    "PROMPT_CACHE_MISS_TOKEN": 30,
                                },
                            },
                        ]
                    }
                ]
            }
        )
        cost_payload = _success_payload(
            {
                "data": [
                    {
                        "currency": "CNY",
                        "series": [
                            {
                                "buckets": [
                                    {"time": jul_1, "cost": "1.235"},
                                    {"time": jul_15, "cost": "0.1"},
                                    {"time": jul_16, "cost": "0.2"},
                                    {"time": jul_17, "cost": "0.3"},
                                ]
                            }
                        ],
                    },
                    {
                        "currency": "USD",
                        "series": [
                            {
                                "buckets": [
                                    {"time": jul_17, "cost": "999"}
                                ]
                            }
                        ],
                    },
                ]
            }
        )

        with patch(
            "api_usage.urlopen",
            side_effect=[
                _FakeResponse(amount_payload),
                _FakeResponse(cost_payload),
            ],
        ) as mocked_urlopen:
            result = api_usage.usage_deepseek(
                dashboard_token="dashboard-token",
                now=datetime(
                    2026,
                    7,
                    17,
                    12,
                    tzinfo=timezone.utc,
                ),
            )

        self.assertEqual(
            result,
            {
                "month": {
                    "tokens": "960.0",
                    "cost_cny": 1.8,
                },
                "today": {
                    "tokens": "130.0",
                    "cost_cny": 0.3,
                },
                "3d": {
                    "cache_hit_percent": 80.0,
                },
            },
        )
        self.assertEqual(mocked_urlopen.call_count, 2)

        amount_request = mocked_urlopen.call_args_list[0].args[0]
        amount_query = parse_qs(urlparse(amount_request.full_url).query)
        self.assertEqual(amount_query["start"], [str(jul_1)])
        self.assertEqual(amount_query["end"], [str(jul_18)])
        self.assertEqual(amount_query["tz"], ["0"])
        self.assertEqual(
            amount_request.get_header("Authorization"),
            "Bearer dashboard-token",
        )

    def test_returns_none_when_three_day_cache_usage_is_zero(self):
        start = datetime(2026, 7, 17, tzinfo=timezone.utc)
        payload = {
            "series": [
                {
                    "buckets": [
                        {
                            "time": int(start.timestamp()),
                            "usage": {
                                "RESPONSE_TOKEN": 10,
                                "PROMPT_CACHE_HIT_TOKEN": 0,
                                "PROMPT_CACHE_MISS_TOKEN": 0,
                            },
                        }
                    ]
                }
            ]
        }

        totals = api_usage._deepseek_token_totals(
            payload,
            start,
            datetime(2026, 7, 18, tzinfo=timezone.utc),
        )

        self.assertIsNone(
            api_usage._cache_hit_percent(
                totals["cache_hit"],
                totals["cache_miss"],
            )
        )

    def test_raises_on_dashboard_business_error(self):
        error_payload = {
            "code": 40003,
            "msg": "Authorization Failed (invalid token)",
            "data": None,
        }

        with patch(
            "api_usage.urlopen",
            return_value=_FakeResponse(error_payload),
        ):
            with self.assertRaisesRegex(
                api_usage.DeepSeekUsageError,
                "40003.*Authorization Failed",
            ):
                api_usage.usage_deepseek(
                    dashboard_token="expired-token",
                    now=datetime(
                        2026,
                        7,
                        17,
                        tzinfo=timezone.utc,
                    ),
                )

    def test_rejects_timezone_naive_now(self):
        with self.assertRaisesRegex(
            ValueError,
            "timezone-aware",
        ):
            api_usage.usage_deepseek(
                dashboard_token="dashboard-token",
                now=datetime(2026, 7, 17),
            )


if __name__ == "__main__":
    unittest.main()
