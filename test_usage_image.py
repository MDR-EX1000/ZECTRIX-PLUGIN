import io
import unittest
from datetime import datetime, timedelta, timezone
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
    def test_lists_the_registered_default_design(self):
        self.assertEqual(
            [design.name for design in usage_image.list_designs()],
            ["daily-grid"],
        )

    def test_rejects_unknown_and_duplicate_designs(self):
        current = datetime(2026, 7, 18, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "unknown design"):
            usage_image.resolve_design_name("missing", current)

        duplicate_designs = (
            usage_image._FunctionDesign("same", "", lambda context: b""),
            usage_image._FunctionDesign("same", "", lambda context: b""),
        )
        with self.assertRaisesRegex(ValueError, "duplicate design name"):
            usage_image.resolve_design_name(
                "rotate",
                current,
                designs=duplicate_designs,
            )

    def test_reset_units_are_rendered_in_lowercase(self):
        canvas = usage_image._Canvas()
        usage_image._draw_progress(
            canvas,
            left=14,
            right=190,
            top=79,
            label="5 HOUR",
            percent=25.0,
            reset_in="5D13H",
        )

        text_values = [operation[1] for operation in canvas._text_ops]
        self.assertIn("RESET 5d13h", text_values)

    def test_usage_percentages_are_rounded_to_whole_numbers(self):
        self.assertEqual(usage_image._whole_number(67.5, "%"), "68%")
        self.assertEqual(usage_image._whole_number(98.65, "%"), "99%")

    def test_deepseek_has_api_chip_next_to_title(self):
        canvas = usage_image._Canvas()
        usage_image._draw_deepseek(canvas, DEEPSEEK_USAGE)

        api_operations = [
            operation
            for operation in canvas._text_ops
            if operation[1] == "API"
        ]
        self.assertEqual(len(api_operations), 1)
        self.assertEqual(api_operations[0][4], 255)

    def test_daily_slogan_is_stable_and_rotates_by_utc_plus_eight_day(self):
        current = datetime(
            2026,
            7,
            17,
            0,
            tzinfo=timezone.utc,
        )
        later_same_day = datetime(
            2026,
            7,
            17,
            15,
            tzinfo=timezone.utc,
        )
        next_day = datetime(
            2026,
            7,
            17,
            16,
            tzinfo=timezone.utc,
        )

        self.assertEqual(
            usage_image.daily_slogan(current),
            "MAY THE CODE BE WITH YOU",
        )
        self.assertEqual(
            usage_image.daily_slogan(current),
            usage_image.daily_slogan(later_same_day),
        )
        self.assertEqual(
            usage_image.daily_slogan(next_day),
            "最后再改亿点点",
        )

    def test_design_scheduler_covers_all_pairs_for_two_designs(self):
        designs = (
            usage_image._FunctionDesign("alpha", "", lambda context: b""),
            usage_image._FunctionDesign("beta", "", lambda context: b""),
        )
        start = datetime(2026, 7, 18, tzinfo=timezone.utc)
        pairs = {
            (
                usage_image.daily_slogan(
                    start + timedelta(days=offset)
                ),
                usage_image.resolve_design_name(
                    "rotate",
                    start + timedelta(days=offset),
                    designs=designs,
                ),
            )
            for offset in range(28)
        }

        self.assertEqual(len(pairs), 28)

    def test_design_scheduler_covers_all_pairs_for_three_designs(self):
        designs = tuple(
            usage_image._FunctionDesign(
                name,
                "",
                lambda context: b"",
            )
            for name in ("alpha", "beta", "gamma")
        )
        start = datetime(2026, 7, 18, tzinfo=timezone.utc)
        pairs = {
            (
                usage_image.daily_slogan(
                    start + timedelta(days=offset)
                ),
                usage_image.resolve_design_name(
                    "rotate",
                    start + timedelta(days=offset),
                    designs=designs,
                ),
            )
            for offset in range(42)
        }

        self.assertEqual(len(pairs), 42)

    def test_design_scheduler_does_not_change_language_alternation(self):
        start = datetime(2026, 7, 18, tzinfo=timezone.utc)
        slogans = [
            usage_image.daily_slogan(
                start + timedelta(days=offset)
            )
            for offset in range(28)
        ]
        languages = [
            "english" if slogan.isascii() else "chinese"
            for slogan in slogans
        ]

        self.assertTrue(
            all(
                languages[index] != languages[index - 1]
                for index in range(1, len(languages))
            )
        )

    def test_renders_800x600_grayscale_png(self):
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
            self.assertEqual(image.size, (800, 600))
            self.assertEqual(image.mode, "L")
            self.assertTrue(
                any(0 < value < 255 for _, value in image.getcolors())
            )

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
            self.assertEqual(image.getpixel((60, 264)), 0)
            self.assertGreater(image.getpixel((200, 264)), 128)

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
            self.assertEqual(image.size, (800, 600))
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
