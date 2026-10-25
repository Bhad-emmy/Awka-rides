import hashlib
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend.main_supabase import (
    AUTH_RATE_LIMIT_MAX,
    AUTH_RATE_LIMIT_WINDOW,
    DriverLogin,
    driver_login,
    find_driver_by_phone,
)


class DriverPhoneLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_accepts_registered_phone_and_pin(self):
        driver = {
            "driver_id": "oka01",
            "name": "Test Driver",
            "phone": "08012345678",
            "plate_number": "ABC123XY",
            "availability_status": "OFFLINE",
            "status": "ACTIVE",
            "verification_status": "VERIFIED",
            "pin_hash": "stored-hash",
        }
        with (
            patch("backend.main_supabase.enforce_shared_rate_limit", new=AsyncMock()) as rate_limit,
            patch("backend.main_supabase.find_driver_by_phone", new=AsyncMock(return_value=driver)) as lookup,
            patch("backend.main_supabase.verify_password", return_value=True),
            patch("backend.main_supabase.create_driver_token", return_value="session-token"),
        ):
            result = await driver_login(DriverLogin(phone=" 08012345678 ", pin="1234"))

        lookup.assert_awaited_once_with(" 08012345678 ")
        rate_limit.assert_awaited_once_with(
            "driver-login:" + hashlib.sha256(b"08012345678").hexdigest(),
            AUTH_RATE_LIMIT_MAX,
            AUTH_RATE_LIMIT_WINDOW,
            "Too many driver login attempts. Please try again later.",
        )
        self.assertTrue(result["authenticated"])
        self.assertEqual(result["token"], "session-token")
        self.assertEqual(result["driver"]["driverId"], "oka01")

    async def test_login_rejects_unknown_phone(self):
        with (
            patch("backend.main_supabase.enforce_shared_rate_limit", new=AsyncMock()),
            patch("backend.main_supabase.find_driver_by_phone", new=AsyncMock(return_value=None)),
        ):
            with self.assertRaises(HTTPException) as caught:
                await driver_login(DriverLogin(phone="08012345678", pin="1234"))
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.detail, "Invalid phone number or PIN.")

    async def test_phone_lookup_rejects_duplicate_accounts(self):
        with patch(
            "backend.main_supabase.supabase_request",
            new=AsyncMock(return_value=[{"driver_id": "oka01"}, {"driver_id": "oka02"}]),
        ):
            with self.assertRaises(HTTPException) as caught:
                await find_driver_by_phone("08012345678")
        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
