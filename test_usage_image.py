import io
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from PIL import Image

import usage_image
from api_usage import DeepSeekUsageError, KimiUsageError


KIMI_USAGE = {
    "5h": {
        "used_percent": 25.0,
        "reset_time": "2026-07-17T15:00:00Z",
        "reset_in": "3h",
    },
    "week": {
        "used_percent": 67.5,
        "reset_time": "2026-07-22T15:00:00Z",
        "reset_in": "5d3h",
    },
    "user_level": "Moderato",
}

DEEPSEEK_USAGE = {
    "month": {
        "tokens": "1.1B",
        "cost_cny": 109.08,
    },
    "today": {
        "tokens": "143.1M",
        "cost_cny": 12.44,
    },
    "3d": {
        "cache_hit_percent": 98.65,
    },
}


class UsageImageTests(unittest.TestCase):
    def test_renders_400x300_monochrome_png(self):
        png = usage_image.render_usage_image(
            KIMI_USAGE,
            DEEPSEEK_USAGE,
            updated_at=datetime(
                2026,
                7,
                17,
                14,
                30,
                tzinfo=timezone.utc,
            ),
        )

        self.assertLess(len(png), 2 * 1024 * 1024)
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (400, 300))
            self.assertEqual(image.mode, "1")
            colors = {value for _, value in image.getcolors()}
            self.assertEqual(colors, {0, 255})

    def test_progress_bar_matches_percentage(self):
        png = usage_image.render_usage_image(
            KIMI_USAGE,
            DEEPSEEK_USAGE,
            updated_at=datetime(
                2026,
                7,
                17,
                tzinfo=timezone.utc,
            ),
        )

        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.getpixel((30, 133)), 0)
            self.assertEqual(image.getpixel((100, 133)), 255)

    def test_renders_partial_data(self):
        png = usage_image.render_usage_image(
            None,
            DEEPSEEK_USAGE,
            updated_at=datetime(
                2026,
                7,
                17,
                tzinfo=timezone.utc,
            ),
        )

        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (400, 300))
            self.assertIsNotNone(image.getbbox())

    def test_collect_usage_keeps_successful_provider(self):
        with (
            patch(
                "usage_image.usage_kimi",
                side_effect=KimiUsageError("Kimi unavailable"),
            ),
            patch(
                "usage_image.usage_deepseek",
                return_value=DEEPSEEK_USAGE,
            ),
        ):
            result = usage_image.collect_usage(
                "both",
                kimi_api_key="kimi-key",
                deepseek_dashboard_token="dashboard-token",
            )

        self.assertIsNone(result.kimi)
        self.assertEqual(result.deepseek, DEEPSEEK_USAGE)
        self.assertEqual(result.errors, {"kimi": "Kimi unavailable"})
        self.assertTrue(result.has_data)

    def test_generate_rejects_when_all_providers_fail(self):
        with (
            patch(
                "usage_image.usage_kimi",
                side_effect=KimiUsageError("Kimi unavailable"),
            ),
            patch(
                "usage_image.usage_deepseek",
                side_effect=DeepSeekUsageError("DeepSeek unavailable"),
            ),
        ):
            with self.assertRaisesRegex(
                usage_image.UsageImageError,
                "No usage provider returned data",
            ):
                usage_image.generate_usage_image(
                    kimi_api_key="kimi-key",
                    deepseek_dashboard_token="dashboard-token",
                )

    def test_rejects_timezone_naive_updated_at(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            usage_image.render_usage_image(
                KIMI_USAGE,
                DEEPSEEK_USAGE,
                updated_at=datetime(2026, 7, 17),
            )


if __name__ == "__main__":
    unittest.main()
