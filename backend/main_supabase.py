import os
import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

load_dotenv()

SUPABASE_TABLE_RIDES = "oka_rides_rides"
SUPABASE_TABLE_DRIVERS = "oka_rides_drivers"
SUPABASE_TABLE_CUSTOMERS = "oka_rides_customers"
PAYSTACK_API_BASE = "https://api.paystack.co"

app = FastAPI(title="Awka Rides API")

configured_frontend_origins = os.getenv("FRONTEND_ORIGIN")
frontend_origins = (
    [origin.strip() for origin in configured_frontend_origins.split(",") if origin.strip()]
    if configured_frontend_origins
    else [
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)


class RideRequest(BaseModel):
    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    email: str = ""
    pickup: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    pickupTiming: str = Field(min_length=1)
    scheduledTime: str = ""
    passengers: str = Field(min_length=1)
    notes: str = ""
    vehicle: str = Field(min_length=1)
    reference: str | None = None
    pickupLatitude: float | None = Field(default=None, ge=-90, le=90)
    pickupLongitude: float | None = Field(default=None, ge=-180, le=180)
    destinationLatitude: float | None = Field(default=None, ge=-90, le=90)
    destinationLongitude: float | None = Field(default=None, ge=-180, le=180)


class DriverCreate(BaseModel):
    driverId: str = Field(min_length=1)
    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    vehicle: str = Field(min_length=1)
    plateNumber: str = Field(min_length=1)
    status: Literal["ACTIVE", "INACTIVE", "SUSPENDED"] = "INACTIVE"
    verificationStatus: Literal["PENDING", "VERIFIED", "REJECTED"] = "PENDING"
    completedRides: int = 0
    cancelledRides: int = 0
    notes: str = ""


class DriverUpdate(BaseModel):
    status: Literal["ACTIVE", "INACTIVE", "SUSPENDED"] | None = None
    verificationStatus: Literal["PENDING", "VERIFIED", "REJECTED"] | None = None
    availabilityStatus: Literal["ONLINE", "OFFLINE", "BUSY"] | None = None
    currentLatitude: float | None = Field(default=None, ge=-90, le=90)
    currentLongitude: float | None = Field(default=None, ge=-180, le=180)
    notes: str | None = None


class FareUpdate(BaseModel):
    fare: float = Field(gt=0)
    fareConfirmed: bool


class PaymentUpdate(BaseModel):
    paymentStatus: Literal["PAID", "FAILED"]
    paymentReference: str = ""
    paymentMethod: Literal["OPAY", "PAYSTACK"]


class RideStatusUpdate(BaseModel):
    status: Literal["PICKED UP", "COMPLETED", "CANCELLED"]


class DispatcherLogin(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class CustomerSignup(BaseModel):
    fullName: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=30)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class CustomerLogin(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class PaystackInitialize(BaseModel):
    reference: str = Field(min_length=1)
    email: str = Field(min_length=3)


RideListStatus = Literal[
    "REQUESTED",
    "DRIVER ASSIGNED",
    "PAID",
    "PICKED UP",
    "COMPLETED",
    "CANCELLED",
]

DISPATCHER_SESSION_COOKIE = "awka_dispatcher_session"
DISPATCHER_SESSION_TTL = 8 * 60 * 60
CUSTOMER_SESSION_COOKIE = "oka_customer_session"
CUSTOMER_SESSION_TTL = 30 * 24 * 60 * 60
PASSWORD_HASH_PREFIX = "pbkdf2_sha256"


def supabase_settings() -> tuple[str, str]:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not service_key:
        raise HTTPException(
            status_code=500,
            detail="Supabase configuration is incomplete on the server.",
        )
    return url, service_key


def supabase_headers(service_key: str) -> dict[str, str]:
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }


async def supabase_request(
    method: str,
    table: str,
    *,
    params: dict[str, str] | None = None,
    json_body: dict | list | None = None,
    action_description: str = "database request",
):
    url, service_key = supabase_settings()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                method,
                f"{url}/rest/v1/{table}",
                headers={
                    **supabase_headers(service_key),
                    "Prefer": "return=representation",
                },
                params=params,
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to connect to Supabase.",
        ) from exc

    if response.is_error:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        detail = (
            payload.get("message")
            or payload.get("hint")
            or payload.get("details")
            or f"Supabase rejected the {action_description}."
        )
        raise HTTPException(status_code=502, detail=str(detail))

    if not response.content:
        return []
    try:
        return response.json()
    except ValueError:
        return []


async def get_ride_record(ride_id: str) -> dict:
    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_RIDES,
        params={"select": "*", "id": f"eq.{ride_id}", "limit": "1"},
        action_description="ride lookup",
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Ride not found.")
    return rows[0]


async def find_ride_by_reference(reference: str) -> dict:
    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_RIDES,
        params={
            "select": "*",
            "reference": f"eq.{reference}",
            "limit": "1",
        },
        action_description="ride lookup",
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Ride not found.")
    return rows[0]


async def get_customer_record(customer_id: str) -> dict:
    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_CUSTOMERS,
        params={
            "select": "id,full_name,phone,email,created_at",
            "id": f"eq.{customer_id}",
            "limit": "1",
        },
        action_description="customer lookup",
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Customer account not found.")
    return rows[0]


async def find_customer_by_email(email: str) -> dict | None:
    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_CUSTOMERS,
        params={
            "select": "*",
            "email": f"eq.{email.lower().strip()}",
            "limit": "1",
        },
        action_description="customer account lookup",
    )
    return rows[0] if rows else None


async def find_customer_by_phone(phone: str) -> dict | None:
    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_CUSTOMERS,
        params={
            "select": "*",
            "phone": f"eq.{phone.strip()}",
            "limit": "1",
        },
        action_description="customer account lookup",
    )
    return rows[0] if rows else None


async def get_driver_record(driver_id: str) -> dict:
    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_DRIVERS,
        params={
            "select": "*",
            "driver_id": f"eq.{driver_id}",
            "limit": "1",
        },
        action_description="driver lookup",
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Driver not found.")
    return rows[0]


def dispatcher_settings():
    username = os.getenv("DISPATCHER_USERNAME")
    password_hash = os.getenv("DISPATCHER_PASSWORD_HASH")
    session_secret = os.getenv("DISPATCHER_SESSION_SECRET")
    if not username or not password_hash or not session_secret:
        raise HTTPException(
            status_code=500,
            detail="Dispatcher authentication is not configured on the server.",
        )
    return username, password_hash, session_secret


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    encoded_salt = base64.urlsafe_b64encode(salt).decode().rstrip("=")
    encoded_digest = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return "pbkdf2_sha256$310000$" + encoded_salt + "$" + encoded_digest


def verify_password(password: str, encoded_hash: str):
    try:
        prefix, iterations_text, salt_text, digest_text = encoded_hash.split("$")
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(
            (salt_text + "=" * (-len(salt_text) % 4)).encode()
        )
        expected_digest = base64.urlsafe_b64decode(
            (digest_text + "=" * (-len(digest_text) % 4)).encode()
        )
    except (ValueError, TypeError, binascii.Error):
        return False

    if prefix != PASSWORD_HASH_PREFIX or iterations < 100_000:
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual_digest, expected_digest)


def sign_session(payload: str, secret: str):
    signature = hmac.new(
        secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{payload}.{encoded_signature}"


def create_session(subject: str, secret: str, ttl: int):
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"sub": subject, "exp": int(time.time()) + ttl},
            separators=(",", ":"),
        ).encode()
    ).decode().rstrip("=")
    return sign_session(payload, secret)


def session_username(cookie: str | None, secret: str):
    if not cookie or "." not in cookie:
        return None

    payload, supplied_signature = cookie.rsplit(".", 1)
    expected_cookie = sign_session(payload, secret)
    expected_signature = expected_cookie.rsplit(".", 1)[1]

    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None

    try:
        padded_payload = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded_payload.encode()))
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
        return None

    if data.get("exp", 0) < int(time.time()):
        return None

    return data.get("sub")


def require_dispatcher_session(
    session_cookie: str | None = Cookie(
        default=None,
        alias=DISPATCHER_SESSION_COOKIE,
    ),
):
    username, _, session_secret = dispatcher_settings()
    if session_username(session_cookie, session_secret) != username:
        raise HTTPException(
            status_code=401,
            detail="Dispatcher authentication required.",
        )
    return username


def customer_session_secret():
    secret = os.getenv("CUSTOMER_SESSION_SECRET") or os.getenv("DISPATCHER_SESSION_SECRET")
    if not secret:
        raise HTTPException(
            status_code=500,
            detail="Customer authentication is not configured on the server.",
        )
    return secret


def require_customer_session(
    session_cookie: str | None = Cookie(
        default=None,
        alias=CUSTOMER_SESSION_COOKIE,
    ),
):
    customer_id = session_username(session_cookie, customer_session_secret())
    if not customer_id:
        raise HTTPException(status_code=401, detail="Customer login required.")
    return customer_id


async def get_customer_ride(reference: str, customer_id: str) -> dict:
    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_RIDES,
        params={
            "select": "*",
            "reference": f"eq.{reference}",
            "customer_id": f"eq.{customer_id}",
            "limit": "1",
        },
        action_description="customer ride lookup",
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Ride not found for this account.")
    return rows[0]


def normalize_nigerian_phone(phone: str) -> str:
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if digits.startswith("234") and len(digits) == 13:
        return digits
    if digits.startswith("0") and len(digits) == 11:
        return "234" + digits[1:]
    raise ValueError("Driver phone must be a Nigerian number such as 08012345678.")


def termii_settings():
    api_key = os.getenv("TERMII_API_KEY")
    sender_id = os.getenv("TERMII_SENDER_ID", "OKA-RIDES")
    channel = os.getenv("TERMII_CHANNEL", "generic").strip().lower()
    base_url = os.getenv(
        "TERMII_BASE_URL",
        "https://api.ng.termii.com/api",
    ).rstrip("/")
    if channel not in {"generic", "dnd"}:
        channel = "generic"
    return api_key, sender_id, channel, base_url


async def send_driver_sms(driver_phone: str, message: str):
    api_key, sender_id, channel, base_url = termii_settings()
    if not api_key:
        return {"status": "SKIPPED", "reason": "TERMII_API_KEY is not configured."}

    try:
        to = normalize_nigerian_phone(driver_phone)
    except ValueError as exc:
        return {"status": "FAILED", "reason": str(exc)}

    payload = {
        "api_key": api_key,
        "to": to,
        "from": sender_id,
        "sms": message,
        "type": "plain",
        "channel": channel,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{base_url}/sms/send",
                json=payload,
            )
    except httpx.HTTPError:
        return {"status": "FAILED", "reason": "Unable to reach SMS provider."}

    if response.is_error:
        try:
            provider_error = response.json()
        except ValueError:
            provider_error = {"message": response.text[:500]}
        return {
            "status": "FAILED",
            "reason": f"SMS provider rejected the message ({response.status_code}).",
            "providerError": provider_error,
        }

    data = response.json()
    return {"status": "SENT", "messageId": data.get("message_id")}


async def send_dispatcher_sms(message: str):
    phone = os.getenv("DISPATCHER_NOTIFICATION_PHONE", "").strip()
    if not phone:
        return {"status": "SKIPPED", "reason": "DISPATCHER_NOTIFICATION_PHONE is not configured."}
    return await send_driver_sms(phone, message)


async def notify_driver_of_assignment(ride: dict, driver: dict):
    reference = ride.get("reference", "N/A")
    customer = ride.get("name", "Customer")
    pickup = ride.get("pickup", "Not provided")
    destination = ride.get("destination", "Not provided")
    fare = ride.get("fare")
    fare_text = (
        f"NGN {fare:,.0f}" if isinstance(fare, (int, float)) else "Fare pending"
    )
    message = (
        f"OKA-RIDES: New ride {reference}. Customer: {customer}. "
        f"Pickup: {pickup}. Destination: {destination}. Fare: {fare_text}. "
        "Check dispatcher for details."
    )
    return await send_driver_sms(driver.get("phone", ""), message)


def ride_representation(record: dict) -> dict:
    return {
        "id": record.get("id"),
        "reference": record.get("reference"),
        "name": record.get("name"),
        "phone": record.get("phone"),
        "email": record.get("email"),
        "pickup": record.get("pickup"),
        "destination": record.get("destination"),
        "pickupTiming": record.get("pickup_timing"),
        "scheduledTime": record.get("scheduled_time"),
        "passengers": record.get("passengers"),
        "notes": record.get("notes"),
        "vehicle": record.get("vehicle"),
        "status": record.get("status"),
        "fare": record.get("fare"),
        "fareConfirmed": record.get("fare_confirmed", False),
        "paymentStatus": record.get("payment_status"),
        "paymentReference": record.get("payment_reference"),
        "paymentMethod": record.get("payment_method"),
        "pickupLatitude": record.get("pickup_latitude"),
        "pickupLongitude": record.get("pickup_longitude"),
        "destinationLatitude": record.get("destination_latitude"),
        "destinationLongitude": record.get("destination_longitude"),
        "driverId": record.get("driver_id"),
        "driverName": record.get("driver_name"),
        "driverPhone": record.get("driver_phone"),
        "driverVehicle": record.get("driver_vehicle"),
        "driverPlateNumber": record.get("driver_plate_number"),
        "createdAt": record.get("created_at"),
        "dispatchAttempts": record.get("dispatch_attempts", 0),
        "dispatchedAt": record.get("dispatched_at"),
    }


def naira_to_kobo(amount: float) -> int:
    if (
        not isinstance(amount, (int, float))
        or isinstance(amount, bool)
        or amount <= 0
    ):
        raise ValueError("Amount must be greater than zero.")
    kobo = round(amount * 100)
    if kobo <= 0:
        raise ValueError("Amount must be greater than zero.")
    return kobo


def paystack_secret():
    secret = os.getenv("PAYSTACK_SECRET_KEY")
    if not secret:
        raise HTTPException(
            status_code=500,
            detail="Paystack is not configured on the server.",
        )
    return secret


def paystack_callback_url():
    callback = os.getenv("PAYSTACK_CALLBACK_URL")
    if not callback:
        raise HTTPException(
            status_code=500,
            detail="Paystack callback URL is not configured.",
        )
    return callback


def paystack_signature_is_valid(
    raw_body: bytes,
    supplied_signature: str | None,
    secret: str,
) -> bool:
    if not supplied_signature:
        return False
    expected = hmac.new(
        secret.encode(),
        raw_body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, supplied_signature)


async def verify_paystack_transaction(
    reference: str,
    expected_amount_kobo: int,
    secret: str,
):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{PAYSTACK_API_BASE}/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {secret}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to verify payment with Paystack.",
        ) from exc

    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Paystack rejected the payment verification request.",
        )

    payload = response.json()
    data = payload.get("data") or {}

    if payload.get("status") is not True or data.get("status") != "success":
        raise HTTPException(
            status_code=402,
            detail="Paystack payment was not successful.",
        )

    if data.get("reference") != reference:
        raise HTTPException(
            status_code=409,
            detail="Paystack reference mismatch.",
        )

    if data.get("currency") != "NGN" or data.get("amount") != expected_amount_kobo:
        raise HTTPException(
            status_code=409,
            detail="Paystack payment amount mismatch.",
        )

    return data


async def fetch_paystack_transaction(reference: str, secret: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{PAYSTACK_API_BASE}/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {secret}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to verify payment with Paystack.",
        ) from exc

    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Paystack verification failed.",
        )

    payload = response.json()
    data = payload.get("data") or {}
    if payload.get("status") is not True or data.get("status") != "success":
        raise HTTPException(
            status_code=402,
            detail="Paystack payment was not successful.",
        )
    return data


async def mark_paystack_paid(record: dict, paystack_data: dict):
    if record.get("payment_status") == "PAID":
        if record.get("payment_reference") == paystack_data.get("reference"):
            return False
        raise HTTPException(
            status_code=409,
            detail="Ride already has a different successful payment.",
        )

    fare = record.get("fare")
    if not record.get("fare_confirmed") or not fare:
        raise HTTPException(
            status_code=409,
            detail="Ride does not have a confirmed fare.",
        )

    expected = naira_to_kobo(float(fare))
    if paystack_data.get("amount") != expected:
        raise HTTPException(
            status_code=409,
            detail="Paystack payment amount mismatch.",
        )

    await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{record['id']}"},
        json_body={
            "payment_status": "PAID",
            "payment_reference": paystack_data["reference"],
            "payment_method": "PAYSTACK",
            "status": "PAID",
        },
        action_description="payment update",
    )
    return True


@app.get("/api/health")
async def health_check():
    await supabase_request(
        "GET",
        SUPABASE_TABLE_RIDES,
        params={"select": "id", "limit": "1"},
        action_description="health check",
    )
    return {
        "status": "ok",
        "message": "Awka Rides API is running",
        "database": "supabase",
    }


@app.post("/api/auth/login")
async def dispatcher_login(credentials: DispatcherLogin, response: Response):
    username, password_hash, session_secret = dispatcher_settings()
    if (
        not hmac.compare_digest(credentials.username, username)
        or not verify_password(credentials.password, password_hash)
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid dispatcher credentials.",
        )

    secure_cookie = os.getenv("DISPATCHER_COOKIE_SECURE", "true").lower() == "true"
    response.set_cookie(
        key=DISPATCHER_SESSION_COOKIE,
        value=create_session(username, session_secret, DISPATCHER_SESSION_TTL),
        max_age=DISPATCHER_SESSION_TTL,
        httponly=True,
        secure=secure_cookie,
        samesite="strict",
        path="/api",
    )
    return {"authenticated": True}


@app.get("/api/auth/session")
async def dispatcher_session(
    _: str = Depends(require_dispatcher_session),
):
    return {"authenticated": True}


@app.post("/api/auth/logout")
async def dispatcher_logout(response: Response):
    response.delete_cookie(
        key=DISPATCHER_SESSION_COOKIE,
        httponly=True,
        secure=os.getenv("DISPATCHER_COOKIE_SECURE", "true").lower() == "true",
        samesite="strict",
        path="/api",
    )
    return {"authenticated": False}


@app.post("/api/rides", status_code=201)
async def create_ride(
    ride: RideRequest,
    customer_id: str = Depends(require_customer_session),
):
    try:
        passengers = str(int(ride.passengers))
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Passengers must be a whole number.",
        ) from exc

    # Booking references are generated server-side so they are unique across
    # phones, browsers, desktop sessions, and simultaneous bookings.
    reference = f"OKA-{secrets.token_hex(4).upper()}"
    customer = await get_customer_record(customer_id)

    payload = {
        "reference": reference,
        "customer_id": customer_id,
        "name": customer.get("full_name"),
        "phone": customer.get("phone"),
        "email": customer.get("email"),
        "pickup": ride.pickup,