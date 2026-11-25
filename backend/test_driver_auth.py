import hashlib
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from pydantic import ValidationError

from backend.main_supabase import (
    AUTH_RATE_LIMIT_MAX,
    AUTH_RATE_LIMIT_WINDOW,
    DriverCreate,
    DriverLogin,
    driver_login,
    find_driver_by_phone,
    csrf_protection_middleware,
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
            patch("backend.main_supabase.enforce_rate_limit", new=Mock()) as rate_limit,
            patch("backend.main_supabase.find_driver_by_phone", new=AsyncMock(return_value=driver)) as lookup,
            patch("backend.main_supabase.verify_password", return_value=True),
            patch("backend.main_supabase.create_driver_token", return_value="session-token"),
        ):
            result = await driver_login(DriverLogin(phone=" 08012345678 ", pin="1234"))

        lookup.assert_awaited_once_with(" 08012345678 ")
        rate_limit.assert_called_once_with(
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
            patch("backend.main_supabase.enforce_rate_limit", new=Mock()),
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


    async def test_legacy_driver_without_pin_is_rejected_without_phone_suffix_fallback(self):
        driver = {
            "driver_id": "oka01",
            "phone": "08012345678",
            "status": "ACTIVE",
            "verification_status": "VERIFIED",
            "pin_hash": None,
        }
        with (
            patch("backend.main_supabase.enforce_rate_limit", new=Mock()),
            patch("backend.main_supabase.find_driver_by_phone", new=AsyncMock(return_value=driver)),
            patch("backend.main_supabase.supabase_request", new=AsyncMock()) as database,
        ):
            with self.assertRaises(HTTPException) as caught:
                await driver_login(DriverLogin(phone="08012345678", pin="5678"))
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("Ask the dispatcher to reset", caught.exception.detail)
        database.assert_not_awaited()


    def test_new_driver_registration_requires_dispatcher_set_pin(self):
        with self.assertRaises(ValidationError):
            DriverCreate(
                driverId="oka-new",
                name="New Driver",
                phone="08012345678",
                vehicle="Keke",
                plateNumber="ABC123XY",
            )


class DriverCsrfMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def make_request(self, headers, cookies=b""):
        scope = {
            "type": "http", "http_version": "1.1", "method": "PATCH",
            "scheme": "https", "path": "/api/driver/availability",
            "raw_path": b"/api/driver/availability", "query_string": b"",
            "root_path": "", "headers": headers,
            "client": ("127.0.0.1", 12345), "server": ("example.test", 443),
        }
        if cookies:
            scope["headers"].append((b"cookie", cookies))
        return Request(scope)

    async def test_driver_bearer_request_is_not_blocked_by_stale_customer_cookie(self):
        request = self.make_request([
            (b"origin", b"https://www.okarides.com.ng"),
            (b"authorization", b"Bearer valid-driver-token"),
        ], b"oka_customer_session=stale-customer-session")

        async def call_next(_request):
            return Response("driver auth dependency will validate bearer token")

        with patch("backend.main_supabase.request_origin_is_allowed", return_value=True):
            response = await csrf_protection_middleware(request, call_next)

        self.assertEqual(response.status_code, 200)

    async def test_driver_mutation_without_bearer_keeps_csrf_protection(self):
        request = self.make_request([
            (b"origin", b"https://www.okarides.com.ng"),
        ], b"oka_customer_session=stale-customer-session")

        async def call_next(_request):
            return Response("should not reach route")

        with patch("backend.main_supabase.request_origin_is_allowed", return_value=True):
            response = await csrf_protection_middleware(request, call_next)

        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF token required", response.body.decode())


if __name__ == "__main__":
    unittest.main()
