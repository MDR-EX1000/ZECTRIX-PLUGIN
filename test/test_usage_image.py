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
            ["daily-grid", "ring-gauge", "big"],
        )

    def test_ring_gauge_design_renders_400x300(self):
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
            design="ring-gauge",
        )

        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (400, 300))
            self.assertEqual(image.mode, "1")
            self.assertEqual(image.getpixel((0, 160)), 0)

    def test_big_design_renders_400x300_with_black_metrics_band(self):
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
            design="big",
        )

        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (400, 300))
            self.assertEqual(image.mode, "1")
            self.assertEqual(image.getpixel((0, 250)), 0)

    def test_big_design_uses_whole_number_quota_percentages(self):
        canvas = usage_image._Canvas()
        usage_image._draw_v3_kimi(canvas, KIMI_USAGE)

        text_values = [operation[1] for operation in canvas._text_ops]
        self.assertIn("25", text_values)
        self.assertIn("68", text_values)
        self.assertNotIn("25.0", text_values)
        self.assertNotIn("67.5", text_values)

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

    def test_reset_units_are_rendered_in_uppercase(self):
        canvas = usage_image._Canvas()
        usage_image._draw_progress(
            canvas,
            left=14,
            right=190,
            top=79,
            label="5 HOUR",
            percent=25.0,
            reset_in="5d13h",
        )

        text_values = [operation[1] for operation in canvas._text_ops]
        self.assertIn("RESET 5D13H", text_values)

    def test_usage_percentages_are_rounded_to_whole_numbers(self):
        self.assertEqual(usage_image._whole_number(67.5, "%"), "68%")

    def test_cache_hit_keeps_one_decimal(self):
        canvas = usage_image._Canvas()
        usage_image._draw_deepseek(canvas, DEEPSEEK_USAGE)

        text_values = [operation[1] for operation in canvas._text_ops]
        self.assertIn("98.7%", text_values)

    def test_chip_background_has_small_rounded_corners(self):
        canvas = usage_image._Canvas()
        usage_image._draw_chip_background(
            canvas,
            left=10,
            top=10,
            right=50,
            bottom=20,
            fill=0,
        )

        self.assertEqual(
            canvas.image.getpixel(
                (usage_image._s(10), usage_image._s(10))
            ),
            255,
        )
        self.assertEqual(
            canvas.image.getpixel(
                (usage_image._s(30), usage_image._s(10))
            ),
            0,
        )

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

    def test_big_design_deepseek_band_has_inverted_api_chip(self):
        canvas = usage_image._Canvas()
        usage_image._draw_v3_deepseek(canvas, DEEPSEEK_USAGE)

        api_operations = [
            operation
            for operation in canvas._text_ops
            if operation[1] == "API"
        ]
        self.assertEqual(len(api_operations), 1)
        self.assertEqual(api_operations[0][4], 0)

    def test_big_design_credit_lockup_is_centered_and_subtle(self):
        canvas = usage_image._Canvas()
        logo = Image.new("L", (24, 24), color=0)
        usage_image._draw_v3_kimi(
            canvas,
            KIMI_USAGE,
            deepseek_logo=logo,
            kimi_logo=logo,
        )

        credit_operations = [
            operation
            for operation in canvas._text_ops
            if operation[1] == "Powered by"
        ]
        self.assertEqual(len(credit_operations), 1)
        credit = credit_operations[0]
        self.assertEqual(credit[3], "credit")
        self.assertEqual(credit[4], usage_image._V3_CREDIT_FILL)
        self.assertGreater(credit[0][0], 250)
        self.assertLess(credit[0][0], 350)

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

    def test_design_scheduler_rotates_two_designs_daily(self):
        designs = (
            usage_image._FunctionDesign("alpha", "", lambda context: b""),
            usage_image._FunctionDesign("beta", "", lambda context: b""),
        )
        start = datetime(2026, 7, 18, tzinfo=timezone.utc)
        names = [
            usage_image.resolve_design_name(
                "rotate",
                start + timedelta(days=offset),
                designs=designs,
            )
            for offset in range(28)
        ]

        self.assertTrue(
            all(names[index] != names[index - 1] for index in range(1, 28))
        )

    def test_default_rotation_excludes_manual_big_design(self):
        start = datetime(2026, 7, 18, tzinfo=timezone.utc)
        names = [
            usage_image.resolve_design_name(
                "rotate",
                start + timedelta(days=offset),
            )
            for offset in range(28)
        ]

        self.assertEqual(set(names), {"daily-grid", "ring-gauge"})
        self.assertEqual(
            usage_image.resolve_design_name("big", start),
            "big",
        )

    def test_design_scheduler_cycles_registered_designs_daily(self):
        designs = tuple(
            usage_image._FunctionDesign(
                name,
                "",
                lambda context: b"",
            )
            for name in ("alpha", "beta", "gamma")
        )
        start = datetime(2026, 7, 18, tzinfo=timezone.utc)
        names = [
            usage_image.resolve_design_name(
                "rotate",
                start + timedelta(days=offset),
                designs=designs,
            )
            for offset in range(42)
        ]

        self.assertEqual(names[3:6], names[:3])
        self.assertEqual(set(names[:3]), {"alpha", "beta", "gamma"})

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

    def test_renders_400x300_1bit_png(self):
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
            self.assertTrue(
                all(value in (0, 255) for _, value in image.getcolors())
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
            design="daily-grid",
        )

        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.getpixel((30, 132)), 0)
            self.assertGreater(image.getpixel((100, 131)), 128)

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
