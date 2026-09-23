import unittest
from unittest.mock import patch

from backend.main import normalize_nigerian_phone, send_driver_sms


class SmsHelperTests(unittest.IsolatedAsyncioTestCase):
    def test_normalize_nigerian_phone(self):
        self.assertEqual(normalize_nigerian_phone("08012345678"), "2348012345678")
        self.assertEqual(normalize_nigerian_phone("2348012345678"), "2348012345678")

    def test_rejects_invalid_phone(self):
        with self.assertRaises(ValueError):
            normalize_nigerian_phone("12345")

    async def test_sms_skips_when_termii_is_not_configured(self):
        with patch.dict("os.environ", {"TERMII_API_KEY": ""}):
            result = await send_driver_sms("08012345678", "test")
        self.assertEqual(result["status"], "SKIPPED")


if __name__ == "__main__":
    unittest.main()
