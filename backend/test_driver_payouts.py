"""Regression tests for the driver payout ledger (no live transfers)."""
import unittest
from unittest.mock import AsyncMock, patch

from backend.main_supabase import (
    DriverPayoutRequest,
    calculate_driver_earning,
    list_driver_earnings,
    mark_driver_earnings_paid_out,
    get_driver_daily_earnings,
)


class DriverPayoutLedgerTests(unittest.IsolatedAsyncioTestCase):
    def test_exact_85_15_split_in_kobo(self):
        self.assertEqual(calculate_driver_earning(150050), (127543, 22507))
        self.assertEqual(calculate_driver_earning(101), (86, 15))
        self.assertEqual(calculate_driver_earning(100), (85, 15))
        with self.assertRaises(ValueError):
            calculate_driver_earning(0)

    async def test_paystack_split_is_not_in_outstanding_manual_payout(self):
        records = [
            {"driver_id": "DRV-1", "driver_name": "Driver A", "status": "PROVIDER_SPLIT",
             "fare_kobo": 10000, "driver_earning_kobo": 8500},
            {"driver_id": "DRV-1", "driver_name": "Driver A", "status": "UNPAID",
             "fare_kobo": 20000, "driver_earning_kobo": 17000},
            {"driver_id": "DRV-1", "driver_name": "Driver A", "status": "PAID_OUT",
             "fare_kobo": 30000, "driver_earning_kobo": 25500},
        ]
        with patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=records)):
            result = await list_driver_earnings(earning_date="2026-11-01", _="dispatcher")
        self.assertEqual(result["earningDate"], "2026-11-01")
        summary = result["drivers"][0]
        self.assertEqual(summary["rideCount"], 3)
        self.assertEqual(summary["earningKobo"], 51000)
        self.assertEqual(summary["providerSplitKobo"], 8500)
        self.assertEqual(summary["paidOutKobo"], 25500)
        self.assertEqual(summary["unpaidKobo"], 17000)
        self.assertEqual(summary["unpaidCount"], 1)

    async def test_bank_payout_requires_atomic_expected_amount_and_count(self):
        confirmation = {
            "success": True, "driverId": "DRV-1", "earningDate": "2026-11-01",
            "rideCount": 2, "amountKobo": 17000, "payoutReference": "BANK-123",
        }
        request = DriverPayoutRequest(
            driverId="DRV-1", earningDate="2026-11-01",
            payoutReference=" BANK-123 ", expectedCount=2, expectedAmountKobo=17000,
        )
        with patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=confirmation)) as db:
            with patch("backend.main_supabase.write_audit_log", new=AsyncMock()) as audit:
                result = await mark_driver_earnings_paid_out(request, dispatcher_id="dispatcher-id")
        self.assertEqual(result["amountKobo"], 17000)
        self.assertEqual(db.await_count, 1)
        args, kwargs = db.await_args
        self.assertEqual(args, ("POST", "rpc/oka_rides_settle_driver_day"))
        self.assertEqual(kwargs["json_body"], {
            "p_driver_id": "DRV-1", "p_earning_date": "2026-11-01",
            "p_reference": "BANK-123", "p_expected_count": 2,
            "p_expected_kobo": 17000, "p_dispatcher_id": "dispatcher-id",
        })
        audit.assert_awaited_once()

    async def test_failed_audit_does_not_disguise_committed_payout(self):
        confirmation = {
            "success": True, "rideCount": 1, "amountKobo": 8500,
            "payoutReference": "BANK-456",
        }
        request = DriverPayoutRequest(
            driverId="DRV-1", earningDate="2026-11-01",
            payoutReference="BANK-456", expectedCount=1, expectedAmountKobo=8500,
        )
        with patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=confirmation)):
            with patch("backend.main_supabase.write_audit_log", new=AsyncMock(side_effect=RuntimeError("logging failed"))):
                result = await mark_driver_earnings_paid_out(request, dispatcher_id="dispatcher-id")
        self.assertTrue(result["success"])



class DriverPrivateEarningsTests(unittest.IsolatedAsyncioTestCase):
    async def test_driver_sees_only_own_daily_earnings(self):
        driver = {
            "driver_id": "oka01", "status": "ACTIVE",
            "verification_status": "VERIFIED",
        }
        entries = [
            {
                "ride_reference": "R-1", "fare_kobo": 10000,
                "driver_earning_kobo": 8500, "payment_method": "PAYSTACK",
                "status": "PROVIDER_SPLIT", "paid_out_at": None,
                "payout_reference": None,
            },
            {
                "ride_reference": "R-2", "fare_kobo": 20000,
                "driver_earning_kobo": 17000, "payment_method": "OFFLINE",
                "status": "UNPAID", "paid_out_at": None,
                "payout_reference": None,
            },
            {
                "ride_reference": "R-3", "fare_kobo": 30000,
                "driver_earning_kobo": 25500, "payment_method": "OFFLINE",
                "status": "PAID_OUT", "paid_out_at": "2026-11-01T12:00:00Z",
                "payout_reference": "BANK-PAID-1",
            },
        ]
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)) as lookup,
            patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=entries)) as db,
        ):
            result = await get_driver_daily_earnings(earning_date="2026-11-01", driver_id="oka01")
        lookup.assert_awaited_once_with("oka01")
        args, kwargs = db.await_args
        self.assertEqual(args, ("GET", "oka_rides_driver_earnings"))
        self.assertEqual(kwargs["params"]["driver_id"], "eq.oka01")
        self.assertEqual(kwargs["params"]["earning_date"], "eq.2026-11-01")
        self.assertNotIn("driver_id", kwargs["params"]["select"])
        self.assertEqual(result["summary"], {
            "rideCount": 3,
            "grossFareKobo": 60000,
            "driverEarningsKobo": 51000,
            "outstandingKobo": 17000,
            "bankPaidKobo": 25500,
            "paystackAllocatedKobo": 8500,
        })
        self.assertEqual(result["rides"][0]["payoutStatus"], "PROVIDER_SPLIT")
        self.assertIsNone(result["rides"][0]["payoutReference"])
        self.assertEqual(result["rides"][2]["payoutReference"], "BANK-PAID-1")

    async def test_driver_earnings_empty_day_is_zero(self):
        driver = {"status": "ACTIVE", "verification_status": "VERIFIED"}
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)),
            patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=[])),
        ):
            result = await get_driver_daily_earnings(earning_date="2026-11-01", driver_id="oka01")
        self.assertEqual(result["rides"], [])
        self.assertEqual(result["summary"]["driverEarningsKobo"], 0)
        self.assertEqual(result["summary"]["rideCount"], 0)

    async def test_unverified_driver_cannot_read_financials(self):
        driver = {"status": "ACTIVE", "verification_status": "PENDING"}
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)),
            patch("backend.main_supabase.supabase_request", new=AsyncMock()) as db,
        ):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as rejected:
                await get_driver_daily_earnings(earning_date="2026-11-01", driver_id="oka01")
            self.assertEqual(rejected.exception.status_code, 403)
            db.assert_not_awaited()

    async def test_invalid_date_cannot_query_ledger(self):
        driver = {"status": "ACTIVE", "verification_status": "VERIFIED"}
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)),
            patch("backend.main_supabase.supabase_request", new=AsyncMock()) as db,
        ):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as rejected:
                await get_driver_daily_earnings(earning_date="2026-02-30", driver_id="oka01")
            self.assertEqual(rejected.exception.status_code, 422)
            db.assert_not_awaited()


class DriverEarningsHttpAuthorizationTests(unittest.TestCase):
    def test_no_driver_token_rejected(self):
        from fastapi.testclient import TestClient
        from backend.main_supabase import app
        with TestClient(app) as client:
            response = client.get("/api/driver/earnings?earning_date=2026-11-01")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
