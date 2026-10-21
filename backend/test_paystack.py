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
    calculate_driver_earning,
    record_driver_earning,
    validate_paystack_subaccount_for_current_key,
    PaystackVerifyRequest,
    verify_customer_paystack_payment,
)


class DriverEarningsLedgerTests(unittest.IsolatedAsyncioTestCase):
    def test_driver_share_uses_existing_85_15_policy_and_balances_exactly(self):
        self.assertEqual(calculate_driver_earning(150050), (127543, 22507))
        self.assertEqual(calculate_driver_earning(100), (85, 15))
        with self.assertRaises(ValueError):
            calculate_driver_earning(0)

    async def test_record_driver_earning_is_idempotent_for_same_paid_ride(self):
        ride = {
            "id": "ride-123", "reference": "OKA-123", "driver_id": "driver-7",
            "driver_name": "Test Driver", "fare": 1500.50,
        }
        existing = [{
            "id": "earning-1", "ride_id": "ride-123", "driver_id": "driver-7",
            "fare_kobo": 150050, "payment_reference": "OKA-PAY-123",
            "driver_earning_kobo": 127543, "platform_commission_kobo": 22507,
            "status": "UNPAID",
        }]
        with patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=existing)) as request:
            result = await record_driver_earning(ride, "OKA-PAY-123", "PAYSTACK")
        self.assertEqual(result["id"], "earning-1")
        request.assert_awaited_once()

    async def test_record_driver_earning_rejects_reference_mismatch(self):
        ride = {
            "id": "ride-123", "reference": "OKA-123", "driver_id": "driver-7",
            "driver_name": "Test Driver", "fare": 1500.50,
        }
        with patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=[{
            "ride_id": "ride-123", "driver_id": "driver-7", "fare_kobo": 150050,
            "payment_reference": "ANOTHER-REFERENCE",
        }])):
            with self.assertRaises(HTTPException) as caught:
                await record_driver_earning(ride, "OKA-PAY-123", "PAYSTACK")
        self.assertEqual(caught.exception.status_code, 409)

    async def test_already_paid_paystack_retry_repairs_missing_earning_row(self):
        ride = {
            "id": "ride-123",
            "reference": "OKA-123",
            "driver_id": "driver-7",
            "driver_name": "Test Driver",
            "fare": 1500.50,
            "fare_confirmed": True,
            "status": "PAID",
            "payment_status": "PAID",
            "payment_method": "PAYSTACK",
            "payment_reference": "OKA-PAY-123",
        }
        with patch("backend.main_supabase.get_customer_ride", new=AsyncMock(return_value=ride)), \\
             patch("backend.main_supabase.record_driver_earning", new=AsyncMock(return_value={"id": "earning-1"})) as record:
            result = await verify_customer_paystack_payment(
                PaystackVerifyRequest(reference="OKA-PAY-123"),
                reference="OKA-123",
                customer_id="customer-1",
            )
        self.assertTrue(result["success"])
        record.assert_awaited_once_with(ride, "OKA-PAY-123", "PAYSTACK")


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


if __name__ == "__main__":
    unittest.main()
