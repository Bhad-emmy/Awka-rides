import hashlib
import hmac
import unittest

from backend.main import naira_to_kobo, paystack_signature_is_valid


class PaystackHelperTests(unittest.TestCase):
    def test_naira_to_kobo(self):
        self.assertEqual(naira_to_kobo(1500), 150000)
        self.assertEqual(naira_to_kobo(1500.50), 150050)

    def test_naira_to_kobo_rejects_non_positive(self):
        with self.assertRaises(ValueError):
            naira_to_kobo(0)
        with self.assertRaises(ValueError):
            naira_to_kobo(-100)

    def test_webhook_signature(self):
        body = b'{"event":"charge.success"}'
        secret = "test_secret"
        signature = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        self.assertTrue(paystack_signature_is_valid(body, signature, secret))
        self.assertFalse(paystack_signature_is_valid(body, "bad", secret))
        self.assertFalse(paystack_signature_is_valid(body, None, secret))


if __name__ == "__main__":
    unittest.main()
