import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend.main_supabase import (
    DriverCreate,
    DriverUpdate,
    create_driver,
    driver_documents_complete,
    ensure_driver_operational,
    driver_session,
)


class DriverDocumentVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_always_creates_inactive_pending_driver(self):
        driver = DriverCreate(
            driverId="oka-test",
            name="Test Driver",
            phone="08012345678",
            vehicle="Keke",
            plateNumber="ABC123XY",
            pin="1234",
            status="ACTIVE",
            verificationStatus="VERIFIED",
        )
        with patch(
            "backend.main_supabase.supabase_request",
            new=AsyncMock(side_effect=[[], [{"driver_id": "oka-test"}]]),
        ) as database:
            await create_driver(driver, "dispatcher")
        payload = database.await_args_list[1].kwargs["json_body"]
        self.assertEqual(payload["status"], "INACTIVE")
        self.assertEqual(payload["verification_status"], "PENDING")
        self.assertEqual(payload["availability_status"], "OFFLINE")
        self.assertTrue(payload["document_review_required"])

    async def test_generic_driver_update_cannot_set_verified(self):
        update = DriverUpdate(verificationStatus="VERIFIED")
        self.assertIsNone(getattr(update, "verificationStatus", None))
        self.assertIsNone(update.status)

    async def test_all_four_latest_documents_must_be_approved_and_current(self):
        rows = [
            {"document_type": "DRIVER_LICENSE", "status": "APPROVED", "expires_at": "2027-12-31"},
            {"document_type": "VEHICLE_REGISTRATION", "status": "APPROVED", "expires_at": None},
            {"document_type": "ROADWORTHINESS", "status": "APPROVED", "expires_at": "2027-12-31"},
            {"document_type": "INSURANCE", "status": "APPROVED", "expires_at": "2027-12-31"},
        ]
        with patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=rows)):
            complete, detail = await driver_documents_complete("oka-test")
        self.assertTrue(complete)
        self.assertEqual(detail["missingOrUnapproved"], [])

    async def test_missing_document_blocks_driver_operations(self):
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value={
                "driver_id": "oka-test", "status": "ACTIVE", "verification_status": "VERIFIED",
                "document_review_required": True
            })),
            patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=[])),
        ):
            with self.assertRaises(HTTPException) as caught:
                await ensure_driver_operational("oka-test")
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("DRIVER_LICENSE", caught.exception.detail["missingOrUnapproved"])

    async def test_existing_active_driver_is_grandfathered_without_document_upload(self):
        driver = {
            "driver_id": "oka-existing",
            "status": "ACTIVE",
            "verification_status": "VERIFIED",
            "availability_status": "ONLINE",
            "document_review_required": False,
        }
        with patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)):
            result = await ensure_driver_operational("oka-existing")
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["availability_status"], "ONLINE")

    async def test_legacy_driver_session_does_not_require_document_table(self):
        driver = {
            "driver_id": "oka-existing",
            "name": "Existing Driver",
            "phone": "08000000000",
            "status": "ACTIVE",
            "verification_status": "VERIFIED",
            "availability_status": "BUSY",
            "document_review_required": False,
        }
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)),
            patch("backend.main_supabase.driver_documents_complete", new=AsyncMock()) as documents_check,
        ):
            result = await driver_session("oka-existing")
        documents_check.assert_not_awaited()
        self.assertEqual(result["driver"]["status"], "ACTIVE")
        self.assertEqual(result["driver"]["availabilityStatus"], "BUSY")
        self.assertTrue(result["driver"]["documentsComplete"])
        self.assertFalse(result["driver"]["documentReviewRequired"])


if __name__ == "__main__":
    unittest.main()
