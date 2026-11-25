import unittest
from unittest.mock import AsyncMock, patch

from backend.main_supabase import (
    DRIVER_DOCUMENT_ENFORCEMENT_ENABLED,
    DriverCreate,
    DriverUpdate,
    DriverDocumentReview,
    auto_dispatch_ride,
    create_driver,
    dispatch_options,
    driver_documents_complete,
    ensure_driver_operational,
    driver_session,
    review_driver_document,
    update_driver,
)


class DriverDocumentVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_enforcement_is_disabled(self):
        self.assertFalse(DRIVER_DOCUMENT_ENFORCEMENT_ENABLED)

    async def test_new_driver_is_active_verified_and_does_not_require_documents(self):
        driver = DriverCreate(
            driverId="oka-test",
            name="Test Driver",
            phone="08012345678",
            vehicle="Keke",
            plateNumber="ABC123XY",
            pin="1234",
        )
        with patch(
            "backend.main_supabase.supabase_request",
            new=AsyncMock(side_effect=[[], [{"driver_id": "oka-test"}]]),
        ) as database:
            await create_driver(driver, "dispatcher")
        payload = database.await_args_list[1].kwargs["json_body"]
        self.assertEqual(payload["status"], "ACTIVE")
        self.assertEqual(payload["verification_status"], "VERIFIED")
        self.assertEqual(payload["availability_status"], "OFFLINE")
        self.assertFalse(payload["document_review_required"])

    async def test_document_helper_remains_available_but_is_not_an_enforcement_gate(self):
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

    async def test_missing_documents_do_not_block_any_driver_even_if_legacy_flag_is_true(self):
        driver = {
            "driver_id": "oka-test",
            "status": "ACTIVE",
            "verification_status": "VERIFIED",
            "availability_status": "ONLINE",
            "document_review_required": True,
        }
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)),
            patch("backend.main_supabase.driver_documents_complete", new=AsyncMock()) as documents_check,
        ):
            result = await ensure_driver_operational("oka-test")
        documents_check.assert_not_awaited()
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["availability_status"], "ONLINE")

    async def test_new_driver_can_be_activated_and_set_online_without_documents(self):
        driver = {
            "driver_id": "oka-incoming",
            "status": "INACTIVE",
            "verification_status": "PENDING",
            "availability_status": "OFFLINE",
            "document_review_required": True,
        }
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)),
            patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=[{"driver_id": "oka-incoming"}])) as database,
            patch("backend.main_supabase.write_audit_log", new=AsyncMock()),
            patch("backend.main_supabase.driver_documents_complete", new=AsyncMock()) as documents_check,
        ):
            await update_driver(DriverUpdate(status="ACTIVE", availabilityStatus="ONLINE"), "oka-incoming", "dispatcher")
        documents_check.assert_not_awaited()
        self.assertEqual(database.await_args.kwargs["json_body"], {
            "status": "ACTIVE",
            "availability_status": "ONLINE",
        })

    async def test_driver_session_never_requires_document_review_while_disabled(self):
        driver = {
            "driver_id": "oka-incoming",
            "name": "Incoming Driver",
            "phone": "08000000000",
            "status": "ACTIVE",
            "verification_status": "VERIFIED",
            "availability_status": "OFFLINE",
            "document_review_required": True,
        }
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(return_value=driver)),
            patch("backend.main_supabase.driver_documents_complete", new=AsyncMock()) as documents_check,
        ):
            result = await driver_session("oka-incoming")
        documents_check.assert_not_awaited()
        self.assertTrue(result["driver"]["documentsComplete"])
        self.assertFalse(result["driver"]["documentReviewRequired"])


    async def test_manual_dispatch_options_include_driver_without_documents(self):
        driver = {
            "driver_id": "oka-incoming",
            "name": "Incoming Driver",
            "phone": "08000000000",
            "vehicle": "Keke",
            "plate_number": "ABC123XY",
            "status": "ACTIVE",
            "verification_status": "VERIFIED",
            "availability_status": "ONLINE",
            "document_review_required": True,
        }
        with (
            patch("backend.main_supabase.get_ride_record", new=AsyncMock(return_value={"id": "ride-1", "status": "REQUESTED"})),
            patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=[driver])),
            patch("backend.main_supabase.driver_documents_complete", new=AsyncMock()) as documents_check,
        ):
            result = await dispatch_options("ride-1", "dispatcher")
        documents_check.assert_not_awaited()
        self.assertTrue(result["drivers"][0]["canAssign"])

    async def test_automatic_dispatch_offers_ride_without_documents(self):
        driver = {
            "driver_id": "oka-incoming",
            "name": "Incoming Driver",
            "phone": "08000000000",
            "vehicle": "Keke",
            "plate_number": "ABC123XY",
            "status": "ACTIVE",
            "verification_status": "VERIFIED",
            "availability_status": "ONLINE",
            "document_review_required": True,
        }
        offered_ride = {"id": "ride-1", "dispatch_offer_expires_at": "2026-12-01T10:00:00+00:00"}
        with (
            patch("backend.main_supabase.supabase_request", new=AsyncMock(side_effect=[[driver], [driver], [offered_ride]])),
            patch("backend.main_supabase.driver_documents_complete", new=AsyncMock()) as documents_check,
            patch("backend.main_supabase.notify_driver_of_assignment", new=AsyncMock()),
            patch("backend.main_supabase.write_audit_log", new=AsyncMock()),
        ):
            result = await auto_dispatch_ride({
                "id": "ride-1",
                "status": "REQUESTED",
                "dispatch_offer_status": "NONE",
                "dispatch_attempts": 0,
            })
        documents_check.assert_not_awaited()
        self.assertEqual(result["status"], "OFFERED")
        self.assertEqual(result["driverId"], "oka-incoming")

    async def test_document_review_never_changes_driver_verification_or_availability(self):
        driver = {
            "driver_id": "oka-incoming",
            "status": "ACTIVE",
            "verification_status": "VERIFIED",
            "availability_status": "BUSY",
            "document_review_required": True,
        }
        with (
            patch("backend.main_supabase.get_driver_record", new=AsyncMock(side_effect=[driver, driver])),
            patch("backend.main_supabase.supabase_request", new=AsyncMock(return_value=[{"document_type": "DRIVER_LICENSE"}])) as database,
            patch("backend.main_supabase.driver_documents_complete", new=AsyncMock()) as documents_check,
            patch("backend.main_supabase.write_audit_log", new=AsyncMock()),
        ):
            result = await review_driver_document(
                DriverDocumentReview(status="REJECTED", note="Review while enforcement disabled"),
                "oka-incoming",
                "document-test-id",
                "dispatcher-test",
            )
        documents_check.assert_not_awaited()
        database.assert_awaited_once()
        self.assertEqual(result["verificationStatus"], "VERIFIED")
        self.assertEqual(result["missingOrUnapproved"], [])
        self.assertEqual(driver["status"], "ACTIVE")
        self.assertEqual(driver["verification_status"], "VERIFIED")
        self.assertEqual(driver["availability_status"], "BUSY")


if __name__ == "__main__":
    unittest.main()
