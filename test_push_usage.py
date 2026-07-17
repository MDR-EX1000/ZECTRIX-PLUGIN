import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from PIL import Image

import push_usage
import usage_image
from test_usage_image import DEEPSEEK_USAGE, KIMI_USAGE


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


def _usage_png():
    return usage_image.render_usage_image(
        KIMI_USAGE,
        DEEPSEEK_USAGE,
        updated_at=datetime(
            2026,
            7,
            17,
            tzinfo=timezone.utc,
        ),
    )


class PushUsageTests(unittest.TestCase):
    def test_pushes_expected_multipart_request(self):
        response = {
            "code": 0,
            "data": {
                "totalPages": 1,
                "pushedPages": 1,
                "pageId": "1",
            },
        }
        with patch(
            "push_usage.urlopen",
            return_value=_FakeResponse(response),
        ) as mocked_urlopen:
            result = push_usage.push_image(
                "zt-secret",
                "AA:BB:CC:DD:EE:FF",
                _usage_png(),
                api_base_url="https://cloud.example",
            )

        self.assertEqual(result["pushedPages"], 1)
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://cloud.example/open/v1/devices/"
            "AA%3ABB%3ACC%3ADD%3AEE%3AFF/display/image",
        )
        headers = {
            key.lower(): value for key, value in request.header_items()
        }
        self.assertEqual(headers["x-api-key"], "zt-secret")
        self.assertTrue(
            headers["content-type"].startswith(
                "multipart/form-data; boundary="
            )
        )
        self.assertIn(b'name="images"', request.data)
        self.assertIn(b'filename="api-usage.png"', request.data)
        self.assertIn(b'name="dither"\r\n\r\nfalse', request.data)
        self.assertIn(b'name="pageId"\r\n\r\n1', request.data)
        self.assertNotIn(b"zt-secret", request.data)

    def test_lists_devices(self):
        response = {
            "code": 0,
            "data": [
                {
                    "deviceId": "AA:BB:CC:DD:EE:FF",
                    "alias": "Desk",
                    "board": "bread-compact-wifi",
                }
            ],
        }
        with patch(
            "push_usage.urlopen",
            return_value=_FakeResponse(response),
        ):
            devices = push_usage.list_zectrix_devices("zt-secret")

        self.assertEqual(
            devices[0]["deviceId"],
            "AA:BB:CC:DD:EE:FF",
        )

    def test_auto_selects_only_device(self):
        with patch(
            "push_usage.list_zectrix_devices",
            return_value=[{"deviceId": "AA:BB:CC:DD:EE:FF"}],
        ):
            device_id = push_usage.resolve_device_id(
                "zt-secret",
                device_id_file="/path/that/does/not/exist",
            )

        self.assertEqual(device_id, "AA:BB:CC:DD:EE:FF")

    def test_rejects_ambiguous_devices(self):
        with patch(
            "push_usage.list_zectrix_devices",
            return_value=[
                {"deviceId": "AA:BB:CC:DD:EE:FF"},
                {"deviceId": "11:22:33:44:55:66"},
            ],
        ):
            with self.assertRaisesRegex(
                push_usage.ZectrixPushError,
                "Multiple Zectrix devices",
            ):
                push_usage.resolve_device_id(
                    "zt-secret",
                    device_id_file="/path/that/does/not/exist",
                )

    def test_rejects_wrong_image_dimensions(self):
        buffer = io.BytesIO()
        Image.new("1", (100, 100), 1).save(buffer, format="PNG")

        with self.assertRaisesRegex(
            push_usage.ZectrixPushError,
            "must be 400x300",
        ):
            push_usage.validate_image(buffer.getvalue())

    def test_redacts_api_key_from_business_error(self):
        response = {
            "code": 40003,
            "msg": "API key zt-secret is invalid",
        }
        with patch(
            "push_usage.urlopen",
            return_value=_FakeResponse(response),
        ):
            with self.assertRaises(push_usage.ZectrixPushError) as raised:
                push_usage.list_zectrix_devices("zt-secret")

        self.assertNotIn("zt-secret", str(raised.exception))
        self.assertIn("[redacted]", str(raised.exception))

    def test_rejects_invalid_page_id(self):
        with self.assertRaisesRegex(ValueError, "page_id"):
            push_usage.push_image(
                "zt-secret",
                "AA:BB:CC:DD:EE:FF",
                _usage_png(),
                page_id="6",
            )


if __name__ == "__main__":
    unittest.main()
