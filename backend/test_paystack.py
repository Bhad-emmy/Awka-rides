import hashlib
import hmac
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend.main_supabase import (
    naira_to_kobo,
    paystack_amount_matches,
    paystack_payment_already_applied,
    paystack_signature_is_valid,
    validate_paystack_transaction,
    paystack_split_config,
    record_payment_ledger,
    validate_paystack_subaccount_for_current_key,
)


class PaystackLedgerSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_reference_cannot_switch_payment_method(self):
        existing = {
            "id": "payment-1",
            "ride_id": "ride-123",
            "amount_kobo": 150050,
            "method": "OFFLINE",
            "status": "PAID",
        }
        with patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=[existing])):
            with self.assertRaises(HTTPException) as caught:
                await record_payment_ledger(
                    {"id": "ride-123", "reference": "RIDE-123"},
                    "SAME-REFERENCE",
                    150050,
                    "PAYSTACK",
                    "PAID",
                    {},
                )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("different payment method", caught.exception.detail)


class PaystackSubaccountEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_subaccount_is_checked_using_configured_paystack_key(self):
        with patch("backend.main_supabase.paystack_api", new=AsyncMock(return_value={
            "subaccount_code": "ACCT_TEST123", "is_active": True,
        })) as api:
            result = await validate_paystack_subaccount_for_current_key("ACCT_TEST123")
        api.assert_awaited_once_with("GET", "/subaccount/ACCT_TEST123")
        self.assertTrue(result["is_active"])

    async def test_rejects_subaccount_not_accessible_with_current_key(self):
        with patch("backend.main_supabase.paystack_api", new=AsyncMock(
            side_effect=HTTPException(status_code=502, detail="Paystack rejected request")
        )):
            with self.assertRaises(HTTPException) as caught:
                await validate_paystack_subaccount_for_current_key("ACCT_LIVE123")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("test keys", caught.exception.detail)

    async def test_rejects_inactive_subaccount(self):
        with patch("backend.main_supabase.paystack_api", new=AsyncMock(return_value={
            "subaccount_code": "ACCT_TEST123", "is_active": False,
        })):
            with self.assertRaises(HTTPException) as caught:
                await validate_paystack_subaccount_for_current_key("ACCT_TEST123")
        self.assertEqual(caught.exception.status_code, 409)


class PaystackHelperTests(unittest.TestCase):
    def setUp(self):
        self.ride = {
            "id": "ride-123",
            "fare": 1500.50,
            "fare_confirmed": True,
            "status": "DRIVER ASSIGNED",
            "payment_status": "PENDING",
            "payment_reference": "OKA-TEST-123",
        }
        self.transaction = {
            "status": "success",
            "reference": "OKA-TEST-123",
            "currency": "NGN",
            "amount": 150050,
            "metadata": {"ride_id": "ride-123"},
        }

    def test_naira_to_kobo(self):
        self.assertEqual(naira_to_kobo(1500), 150000)
        self.assertEqual(naira_to_kobo(1500.50), 150050)

    def test_naira_to_kobo_rejects_non_positive(self):
        for amount in (0, -100, None, "not-a-number"):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    naira_to_kobo(amount)

    def test_exact_amount_matching(self):
        self.assertTrue(paystack_amount_matches(150050, 1500.50))
        self.assertFalse(paystack_amount_matches(150049, 1500.50))
        self.assertFalse(paystack_amount_matches(None, 1500.50))

    def test_payment_idempotency_helper(self):
        paid_ride = {
            "payment_status": "PAID",
            "payment_reference": "OKA-TEST-123",
            "status": "PAID",
        }
        self.assertTrue(paystack_payment_already_applied(paid_ride, "OKA-TEST-123"))
        self.assertFalse(paystack_payment_already_applied(paid_ride, "OTHER"))

    def test_webhook_signature(self):
        body = b'{"event":"charge.success"}'
        secret = "test_secret"
        signature = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        self.assertTrue(paystack_signature_is_valid(body, signature, secret))
        self.assertFalse(paystack_signature_is_valid(body, "bad", secret))
        self.assertFalse(paystack_signature_is_valid(body, None, secret))

    def test_split_config_routes_85_percent_to_driver_and_platform_keeps_15_percent(self):
        config = paystack_split_config({"paystack_subaccount_code": "ACCT_DRIVER123"})
        self.assertEqual(config, {
            "split": {
                "type": "percentage",
                "bearer_type": "account",
                "subaccounts": [{"subaccount": "ACCT_DRIVER123", "share": 85}],
            },
        })

    def test_split_config_rejects_missing_or_invalid_subaccount(self):
        for driver in ({}, {"paystack_subaccount_code": "invalid"}):
            with self.subTest(driver=driver):
                with self.assertRaises(HTTPException):
                    paystack_split_config(driver)

    def test_valid_transaction_matches_ride(self):
        validate_paystack_transaction(self.transaction, self.ride, "OKA-TEST-123")

    def test_wrong_amount_is_rejected(self):
        transaction = {**self.transaction, "amount": 150049}
        with self.assertRaises(HTTPException):
            validate_paystack_transaction(transaction, self.ride, "OKA-TEST-123")

    def test_wrong_ride_metadata_is_rejected(self):
        transaction = {**self.transaction, "metadata": {"ride_id": "other-ride"}}
        with self.assertRaises(HTTPException):
            validate_paystack_transaction(transaction, self.ride, "OKA-TEST-123")


class PlatformCollectionCheckoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_collects_full_fare_without_split_or_driver_subaccount(self):
        from fastapi import Request
        from backend.main_supabase import initialize_paystack_payment
        ride = {
            "id": "ride-test", "reference": "R-1",
            "status": "DRIVER ASSIGNED", "payment_status": "PENDING",
            "fare": 1000.0, "fare_confirmed": True,
            "email": "customer@example.com", "driver_id": "oka-test",
        }
        driver = {"driver_id": "oka-test", "status": "ACTIVE",
                  "verification_status": "VERIFIED", "paystack_subaccount_code": None}
        checkout = {"authorization_url": "https://checkout.paystack.com/test-token"}
        request = Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                           "headers": [], "server": ("localhost", 8000),
                           "scheme": "https", "client": ("127.0.0.1", 1234)})
        with (
            patch("backend.main_supabase.get_customer_ride", new=AsyncMock(return_value=ride)),
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)),
            patch("backend.main_supabase.supabase_request",
                  new=AsyncMock(return_value=[{"id": "ride-test"}])) as database,
            patch("backend.main_supabase.paystack_api", new=AsyncMock(return_value=checkout)) as provider,
            patch("backend.main_supabase.secrets.token_hex", return_value="aabbcc"),
        ):
            result = await initialize_paystack_payment(request, reference="R-1", customer_id="customer-1")
        self.assertEqual(result["amount"], 100000)
        provider_payload = provider.await_args.args[2]
        self.assertEqual(provider_payload["amount"], 100000)
        self.assertEqual(provider_payload["currency"], "NGN")
        self.assertEqual(provider_payload["metadata"]["settlement_mode"], "PLATFORM_MANUAL")
        for forbidden in ("split", "split_code", "subaccount", "transaction_charge"):
            self.assertNotIn(forbidden, provider_payload)
        self.assertEqual(database.await_args.kwargs["json_body"]["paystack_settlement_mode"], "MANUAL")

    async def test_new_paystack_checkout_requires_real_eligible_driver_but_not_subaccount(self):
        from fastapi import Request
        from backend.main_supabase import initialize_paystack_payment
        ride = {"id": "ride-test", "reference": "R-1",
                "status": "DRIVER ASSIGNED", "payment_status": "PENDING",
                "fare": 1000, "fare_confirmed": True,
                "email": "customer@example.com", "driver_id": "oka-test"}
        request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
        with (
            patch("backend.main_supabase.get_customer_ride", new=AsyncMock(return_value=ride)),
            patch("backend.main_supabase.get_driver_record",
                  new=AsyncMock(return_value={"status": "INACTIVE", "verification_status": "VERIFIED"})),
            patch("backend.main_supabase.paystack_api", new=AsyncMock()) as api,
        ):
            with self.assertRaises(HTTPException) as rejection:
                await initialize_paystack_payment(request, reference="R-1", customer_id="customer-1")
        self.assertEqual(rejection.exception.status_code, 409)
        api.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
