"""Paystack driver onboarding regression tests. Provider and database are mocked."""
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from backend.main_supabase import (
    DriverBankLookup,
    DriverPaystackConnect,
    DriverPaystackLink,
    create_driver_paystack_subaccount,
    link_driver_paystack_subaccount,
    lookup_driver_bank,
    paystack_admin_readiness,
    paystack_subaccount_public_info,
    list_paystack_banks,
    validate_paystack_subaccount_for_current_key,
    safe_paystack_mode,
)


class DriverPaystackOnboardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.driver = {
            "driver_id": "oka04", "name": "Test Driver",
            "phone": "08000000000", "status": "ACTIVE",
            "verification_status": "VERIFIED",
            "paystack_subaccount_code": None,
        }
        self.provider_account = {
            "subaccount_code": "ACCT_DRIVER4",
            "account_number": "0000000000",
            "account_name": "TEST DRIVER",
            "settlement_bank": "Test Bank",
            "domain": "test", "active": True, "is_verified": False,
        }

    async def test_paystack_mode_never_exposes_secret(self):
        with patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": "sk_test_PRIVATEVALUE"}, clear=False):
            self.assertEqual(safe_paystack_mode(), "test")
            with patch("backend.main_supabase.paystack_api", new=AsyncMock(return_value=[{"code":"001"}])) as api:
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

    async def test_create_85_15_subaccount_and_store_code_not_bank_number(self):
        request = DriverPaystackConnect(
            bankCode="001", accountNumber="0000000000",
            confirmedAccountName="Test  Driver", confirmedOwnerConsent=True,
        )
        async def provider(method, path, payload=None):
            if method=="POST":
                return {"subaccount_code": "ACCT_DRIVER4"}
            raise AssertionError("Unexpected direct provider request")
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=self.driver)),
            patch("backend.main_supabase.enforce_shared_rate_limit", new=AsyncMock()),
            patch("backend.main_supabase.resolve_driver_bank_account",
                  new=AsyncMock(return_value={"account_name": "TEST DRIVER", "account_number": "0000000000"})),
            patch("backend.main_supabase.paystack_api", new=AsyncMock(side_effect=provider)) as api,
            patch("backend.main_supabase.validate_paystack_subaccount_for_current_key",
                  new=AsyncMock(return_value=self.provider_account)),
            patch("backend.main_supabase.supabase_request",
                  new=AsyncMock(return_value=[{"driver_id": "oka04"}])) as db,
            patch("backend.main_supabase.write_audit_log", new=AsyncMock()),
            patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": "sk_test_VALIDMOCK"}, clear=False),
        ):
            result = await create_driver_paystack_subaccount(request, driver_id="oka04", dispatcher_id="dispatcher")
        self.assertTrue(result["connected"])
        self.assertEqual(result["subaccountCode"], "ACCT_DRIVER4")
        self.assertTrue(result["requiresDashboardVerification"])
        self.assertNotIn("0000000000", str(result))
        provider_payload = api.await_args.args[2]
        self.assertEqual(provider_payload["percentage_charge"], 15)
        self.assertEqual(provider_payload["bank_code"], "001")
        self.assertEqual(provider_payload["account_number"], "0000000000")
        params = db.await_args.kwargs
        self.assertEqual(params["json_body"], {"paystack_subaccount_code": "ACCT_DRIVER4"})
        self.assertEqual(params["params"]["paystack_subaccount_code"], "is.null")
        self.assertNotIn("account_number", params["json_body"])

    async def test_requires_explicit_driver_consent(self):
        request = DriverPaystackConnect(
            bankCode="001", accountNumber="0000000000",
            confirmedAccountName="TEST DRIVER", confirmedOwnerConsent=False,
        )
        with patch("backend.main_supabase.paystack_api", new=AsyncMock()) as api:
            with self.assertRaises(HTTPException) as failure:
                await create_driver_paystack_subaccount(request, driver_id="oka04", dispatcher_id="dispatcher")
        self.assertEqual(failure.exception.status_code, 422)
        api.assert_not_awaited()

    async def test_rejects_changed_account_name_before_provider_creation(self):
        request = DriverPaystackConnect(
            bankCode="001", accountNumber="0000000000",
            confirmedAccountName="SOMEONE ELSE", confirmedOwnerConsent=True,
        )
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=self.driver)),
            patch("backend.main_supabase.enforce_shared_rate_limit", new=AsyncMock()),
            patch("backend.main_supabase.resolve_driver_bank_account",
                  new=AsyncMock(return_value={"account_name": "TEST DRIVER"})),
            patch("backend.main_supabase.paystack_api", new=AsyncMock()) as api,
        ):
            with self.assertRaises(HTTPException) as failure:
                await create_driver_paystack_subaccount(request, driver_id="oka04", dispatcher_id="dispatcher")
        self.assertEqual(failure.exception.status_code, 409)
        api.assert_not_awaited()

    async def test_refuses_duplicate_linked_driver_without_provider_call(self):
        linked = {**self.driver, "paystack_subaccount_code": "ACCT_EXISTING"}
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=linked)),
            patch("backend.main_supabase.paystack_api", new=AsyncMock()) as api,
        ):
            with self.assertRaises(HTTPException) as failure:
                await link_driver_paystack_subaccount(
                    DriverPaystackLink(subaccountCode="ACCT_DRIVER4", confirmedOwnerConsent=True),
                    driver_id="oka04", dispatcher_id="dispatcher",
                )
        self.assertEqual(failure.exception.status_code, 409)
        api.assert_not_awaited()

    async def test_existing_subaccount_code_cannot_be_shared_between_drivers(self):
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=self.driver)),
            patch("backend.main_supabase.validate_paystack_subaccount_for_current_key",
                  new=AsyncMock(return_value=self.provider_account)),
            patch("backend.main_supabase.supabase_request",
                  new=AsyncMock(return_value=[{"driver_id": "oka01"}])) as db,
        ):
            with self.assertRaises(HTTPException) as failure:
                await link_driver_paystack_subaccount(
                    DriverPaystackLink(subaccountCode="ACCT_DRIVER4", confirmedOwnerConsent=True),
                    driver_id="oka04", dispatcher_id="dispatcher",
                )
        self.assertEqual(failure.exception.status_code, 409)
        db.assert_awaited_once()

    async def test_paystack_subaccount_active_and_mode_must_match(self):
        with (
            patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": "sk_test_VALIDMOCK"}, clear=False),
            patch("backend.main_supabase.paystack_api",
                  new=AsyncMock(return_value={**self.provider_account, "active": False})),
        ):
            with self.assertRaises(HTTPException) as e:
                await validate_paystack_subaccount_for_current_key("ACCT_DRIVER4")
        self.assertEqual(e.exception.status_code, 409)
        with (
            patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": "sk_test_VALIDMOCK"}, clear=False),
            patch("backend.main_supabase.paystack_api",
                  new=AsyncMock(return_value={**self.provider_account, "domain": "live"})),
        ):
            with self.assertRaises(HTTPException) as e:
                await validate_paystack_subaccount_for_current_key("ACCT_DRIVER4")
        self.assertEqual(e.exception.status_code, 409)

    async def test_public_info_masks_bank_number(self):
        detail = paystack_subaccount_public_info(self.provider_account)
        self.assertEqual(detail["maskedAccount"], "****0000")
        self.assertNotIn("0000000000", str(detail))

    async def test_bank_directory_sorts_supported_banks(self):
        sample = [{"code":"002","name":"Zed Bank","active":True},
                  {"code":"001","name":"Alpha Bank","active":True}]
        with patch("backend.main_supabase.paystack_api", new=AsyncMock(return_value=sample)):
            result = await list_paystack_banks(_="dispatcher")
        self.assertEqual([x["code"] for x in result["banks"]], ["001","002"])



class ManualBankPayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_bank_profile_never_sends_number_to_driver(self):
        from backend.main_supabase import DriverManualBankProfile, save_driver_manual_payout_bank
        data=DriverManualBankProfile(bankCode="001", bankName="Test Bank",
                                     accountNumber="0000000000",confirmedAccountName="TEST DRIVER",
                                     confirmedOwnerConsent=True)
        driver={"driver_id":"oka01","status":"ACTIVE","verification_status":"VERIFIED"}
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)),
            patch("backend.main_supabase.enforce_shared_rate_limit",new=AsyncMock()),
            patch("backend.main_supabase.resolve_driver_bank_account",
                  new=AsyncMock(return_value={"account_name":"TEST DRIVER","account_number":"0000000000"})),
            patch("backend.main_supabase.supabase_request",
                  new=AsyncMock(return_value=[{"driver_id":"oka01"}])) as db,
            patch("backend.main_supabase.write_audit_log",new=AsyncMock()),
        ):
            result=await save_driver_manual_payout_bank(data,driver_id="oka01",dispatcher_id="dispatcher")
        self.assertTrue(result["saved"])
        self.assertEqual(result["maskedAccount"], "****0000")
        self.assertNotIn("0000000000",str(result))
        self.assertEqual(db.await_args.kwargs["json_body"]["payout_account_number"],"0000000000")
        self.assertNotIn("paystack_subaccount_code",db.await_args.kwargs["json_body"])

    async def test_dispatcher_can_retrieve_verified_bank(self):
        from backend.main_supabase import get_driver_manual_payout_bank
        driver={"payout_bank_verified_at":"2026-11-01T18:00:00Z",
                "payout_bank_name":"Test Bank","payout_bank_code":"001",
                "payout_account_name":"TEST DRIVER","payout_account_number":"0000000000"}
        with patch("backend.main_supabase.get_driver_record",
                   new=AsyncMock(return_value=driver)):
            result=await get_driver_manual_payout_bank(driver_id="oka01",_="dispatcher")
        self.assertEqual(result["accountNumber"],"0000000000")

    async def test_missing_bank_owner_consent_rejected(self):
        from backend.main_supabase import DriverManualBankProfile, save_driver_manual_payout_bank
        data=DriverManualBankProfile(bankCode="001",bankName="Test Bank",
                accountNumber="0000000000",confirmedAccountName="TEST DRIVER",confirmedOwnerConsent=False)
        with patch("backend.main_supabase.supabase_request",new=AsyncMock()) as db:
            with self.assertRaises(HTTPException) as failure:
                await save_driver_manual_payout_bank(data,driver_id="oka01",dispatcher_id="dispatcher")
        self.assertEqual(failure.exception.status_code,422)
        db.assert_not_awaited()

if __name__ == "__main__":
    unittest.main()
