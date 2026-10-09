import hashlib
import hmac
import unittest

from fastapi import HTTPException

from backend.main_supabase import (
    naira_to_kobo,
    paystack_amount_matches,
    paystack_payment_already_applied,
    paystack_signature_is_valid,
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
