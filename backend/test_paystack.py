import hashlib
import hmac
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.main_supabase import (
    naira_to_kobo,
    paystack_amount_matches,
    paystack_secret_key,
    paystack_payment_already_applied,
    paystack_signature_is_valid,
    validate_paystack_subaccount,
    validate_paystack_transaction,
)


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
            "domain": "test",
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

    def test_test_mode_rejects_live_secret_key(self):
        with patch.dict(os.environ, {"PAYSTACK_MODE": "test", "PAYSTACK_SECRET_KEY": "sk_live_fake"}):
            with self.assertRaises(HTTPException) as error:
                paystack_secret_key()
            self.assertEqual(error.exception.status_code, 503)

    def test_test_mode_accepts_test_secret_key(self):
        with patch.dict(os.environ, {"PAYSTACK_MODE": "test", "PAYSTACK_SECRET_KEY": "sk_test_fake"}):
            self.assertEqual(paystack_secret_key(), "sk_test_fake")

    def test_subaccount_must_route_fifteen_percent_to_platform(self):
        validate_paystack_subaccount(
            {"subaccount_code": "ACCT_TEST123", "percentage_charge": 15, "active": True, "domain": "test"},
            "ACCT_TEST123",
        )

    def test_subaccount_with_wrong_percentage_is_rejected(self):
        with self.assertRaises(HTTPException):
            validate_paystack_subaccount(
                {"subaccount_code": "ACCT_TEST123", "percentage_charge": 85, "active": True, "domain": "test"},
                "ACCT_TEST123",
            )

    def test_subaccount_code_mismatch_is_rejected(self):
        with self.assertRaises(HTTPException):
            validate_paystack_subaccount(
                {"subaccount_code": "ACCT_OTHER123", "percentage_charge": 15, "active": True, "domain": "test"},
                "ACCT_TEST123",
            )

    def test_live_subaccount_is_rejected_in_test_mode(self):
        with patch.dict(os.environ, {"PAYSTACK_MODE": "test"}):
            with self.assertRaises(HTTPException):
                validate_paystack_subaccount(
                    {"subaccount_code": "ACCT_TEST123", "percentage_charge": 15, "active": True, "domain": "live"},
                    "ACCT_TEST123",
                )

    def test_valid_transaction_matches_ride(self):
        validate_paystack_transaction(self.transaction, self.ride, "OKA-TEST-123")

    def test_split_transaction_must_use_expected_subaccount(self):
        transaction = {**self.transaction, "subaccount": {"subaccount_code": "ACCT_TEST123"}}
        validate_paystack_transaction(transaction, self.ride, "OKA-TEST-123", "ACCT_TEST123")
        with self.assertRaises(HTTPException):
            validate_paystack_transaction(transaction, self.ride, "OKA-TEST-123", "ACCT_OTHER123")

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
