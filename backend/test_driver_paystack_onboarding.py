"""Paystack bank verification and manual payout-bank regression tests."""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend.main_supabase import (
    DriverBankLookup,
    app,
    lookup_driver_bank,
    paystack_admin_readiness,
    list_paystack_banks,
    safe_paystack_mode,
)


class DriverPaystackOnboardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.driver = {
            "driver_id": "oka04", "name": "Test Driver",
            "phone": "08000000000", "status": "ACTIVE",
            "verification_status": "VERIFIED",
        }

    async def test_paystack_mode_never_exposes_secret(self):
        with patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": "sk_test_PRIVATEVALUE"}, clear=False):
            self.assertEqual(safe_paystack_mode(), "test")
            with patch("backend.main_supabase.paystack_api", new=AsyncMock(return_value=[{"code": "001"}])) as api:
                result = await paystack_admin_readiness(_="dispatcher")
        self.assertTrue(result["apiConnected"])
        self.assertEqual(result["mode"], "test")
        self.assertNotIn("PRIVATEVALUE", str(result))
        api.assert_awaited_once_with("GET", "/bank?country=nigeria&perPage=1")

    async def test_bank_lookup_returns_account_name_only(self):
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=self.driver)),
            patch("backend.main_supabase.enforce_shared_rate_limit", new=AsyncMock()),
            patch("backend.main_supabase.resolve_driver_bank_account",
                  new=AsyncMock(return_value={"account_name": "TEST DRIVER", "account_number": "0000000000"})),
        ):
            result = await lookup_driver_bank(
                DriverBankLookup(bankCode="001", accountNumber="0000000000"),
                driver_id="oka04", _="dispatcher",
            )
        self.assertEqual(result, {"accountName": "TEST DRIVER", "maskedAccount": "****0000"})
        self.assertNotIn("0000000000", str(result))

    async def test_bank_directory_sorts_supported_banks(self):
        sample = [{"code": "002", "name": "Zed Bank", "active": True},
                  {"code": "001", "name": "Alpha Bank", "active": True}]
        with patch("backend.main_supabase.paystack_api", new=AsyncMock(return_value=sample)):
            result = await list_paystack_banks(_="dispatcher")
        self.assertEqual([x["code"] for x in result["banks"]], ["001", "002"])

    def test_legacy_subaccount_routes_are_not_registered(self):
        paths = {route.path for route in app.routes}
        self.assertNotIn("/api/drivers/{driver_id}/paystack/connect", paths)
        self.assertNotIn("/api/drivers/{driver_id}/paystack/link", paths)


class ManualBankPayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_bank_profile_never_sends_number_to_driver(self):
        from backend.main_supabase import DriverManualBankProfile, save_driver_manual_payout_bank
        data = DriverManualBankProfile(
            bankCode="001", bankName="Test Bank", accountNumber="0000000000",
            confirmedAccountName="TEST DRIVER", confirmedOwnerConsent=True,
        )
        driver = {"driver_id": "oka01", "status": "ACTIVE", "verification_status": "VERIFIED"}
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)),
            patch("backend.main_supabase.enforce_shared_rate_limit", new=AsyncMock()),
            patch("backend.main_supabase.resolve_driver_bank_account",
                  new=AsyncMock(return_value={"account_name": "TEST DRIVER", "account_number": "0000000000"})),
            patch("backend.main_supabase.supabase_request",
                  new=AsyncMock(return_value=[{"driver_id": "oka01"}])) as db,
            patch("backend.main_supabase.write_audit_log", new=AsyncMock()),
        ):
            result = await save_driver_manual_payout_bank(data, driver_id="oka01", dispatcher_id="dispatcher")
        self.assertTrue(result["saved"])
        self.assertEqual(result["maskedAccount"], "****0000")
        self.assertNotIn("0000000000", str(result))
        self.assertEqual(db.await_args.kwargs["json_body"]["payout_account_number"], "0000000000")
        self.assertNotIn("paystack_subaccount_code", db.await_args.kwargs["json_body"])

    async def test_dispatcher_can_retrieve_verified_bank(self):
        from backend.main_supabase import get_driver_manual_payout_bank
        driver = {
            "payout_bank_verified_at": "2026-11-01T18:00:00Z",
            "payout_bank_name": "Test Bank", "payout_bank_code": "001",
            "payout_account_name": "TEST DRIVER", "payout_account_number": "0000000000",
        }
        with patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)):
            result = await get_driver_manual_payout_bank(driver_id="oka01", _="dispatcher")
        self.assertEqual(result["accountNumber"], "0000000000")

    async def test_missing_bank_owner_consent_rejected(self):
        from backend.main_supabase import DriverManualBankProfile, save_driver_manual_payout_bank
        data = DriverManualBankProfile(
            bankCode="001", bankName="Test Bank", accountNumber="0000000000",
            confirmedAccountName="TEST DRIVER", confirmedOwnerConsent=False,
        )
        with patch("backend.main_supabase.supabase_request", new=AsyncMock()) as db:
            with self.assertRaises(HTTPException) as failure:
                await save_driver_manual_payout_bank(data, driver_id="oka01", dispatcher_id="dispatcher")
        self.assertEqual(failure.exception.status_code, 422)
        db.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
