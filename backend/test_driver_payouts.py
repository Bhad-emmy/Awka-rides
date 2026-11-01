"""Regression tests for the driver payout ledger (no live transfers)."""
import unittest
from unittest.mock import AsyncMock, patch

from backend.main_supabase import (
    DriverPayoutRequest,
    calculate_driver_earning,
    list_driver_earnings,
    mark_driver_earnings_paid_out,
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


if __name__ == "__main__":
    unittest.main()
