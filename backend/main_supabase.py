import os
import base64
import binascii
import hashlib
import hmac
import time
from collections import defaultdict, deque
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

# Authentication rate limiting. V1 uses per-instance memory; configure via env vars.
AUTH_RATE_LIMIT_MAX = int(os.getenv("AUTH_RATE_LIMIT_MAX", "5"))
AUTH_RATE_LIMIT_WINDOW = int(os.getenv("AUTH_RATE_LIMIT_WINDOW", "900"))
SIGNUP_RATE_LIMIT_MAX = int(os.getenv("SIGNUP_RATE_LIMIT_MAX", "5"))
RIDE_RATE_LIMIT_MAX = int(os.getenv("RIDE_RATE_LIMIT_MAX", "5"))
PAYMENT_RATE_LIMIT_MAX = int(os.getenv("PAYMENT_RATE_LIMIT_MAX", "5"))
ACTION_RATE_LIMIT_WINDOW = int(os.getenv("ACTION_RATE_LIMIT_WINDOW", "900"))
_rate_limit_buckets = defaultdict(deque)

def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")

def enforce_rate_limit(key: str, maximum: int, window: int, detail: str):
    now = time.monotonic()
    bucket = _rate_limit_buckets[key]
    while bucket and now - bucket[0] >= window:
        bucket.popleft()
    if len(bucket) >= maximum:
        retry_after = max(1, int(window - (now - bucket[0])))
        raise HTTPException(status_code=429, detail=detail, headers={"Retry-After": str(retry_after)})
    bucket.append(now)

def enforce_auth_rate_limit(request: Request):
    enforce_rate_limit("auth:" + _client_ip(request), AUTH_RATE_LIMIT_MAX, AUTH_RATE_LIMIT_WINDOW, "Too many authentication attempts. Please try again later.")



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


class CustomerGoogleLogin(BaseModel):
    accessToken: str = Field(min_length=20)
    phone: str = Field(default="", max_length=30)


class CustomerProfileUpdate(BaseModel):
    fullName: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=30)
    currentPassword: str = Field(default="", max_length=128)


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
CUSTOMER_SESSION_TTL = 14 * 24 * 60 * 60
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
    secret = os.getenv("CUSTOMER_SESSION_SECRET")
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
    base_url = os.getenv(
        "TERMII_BASE_URL",
        "https://api.ng.termii.com/api",
    ).rstrip("/")
    channel = os.getenv("TERMII_CHANNEL", "generic").strip().lower()
    if channel not in {"generic", "dnd"}:
        channel = "generic"
    return api_key, sender_id, base_url, channel


async def send_driver_sms(driver_phone: str, message: str):
    api_key, sender_id, base_url, channel = termii_settings()
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
            provider_error = response.text[:1000]
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
async def dispatcher_login(credentials: DispatcherLogin, response: Response, request: Request):
    enforce_auth_rate_limit(request)
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
    request: Request,
    ride: RideRequest,
    customer_id: str = Depends(require_customer_session),
):
    enforce_rate_limit("ride:" + str(customer_id), RIDE_RATE_LIMIT_MAX, ACTION_RATE_LIMIT_WINDOW, "Too many ride requests. Please try again later.")

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
        "destination": ride.destination,
        "pickup_timing": ride.pickupTiming,
        "scheduled_time": ride.scheduledTime or None,
        "passengers": passengers,
        "notes": ride.notes,
        "vehicle": ride.vehicle,
        "status": "REQUESTED",
        "fare_confirmed": False,
        "payment_status": "PENDING",
        "pickup_latitude": ride.pickupLatitude,
        "pickup_longitude": ride.pickupLongitude,
        "destination_latitude": ride.destinationLatitude,
        "destination_longitude": ride.destinationLongitude,
    }

    rows = await supabase_request(
        "POST",
        SUPABASE_TABLE_RIDES,
        json_body=payload,
        action_description="ride request",
    )
    created = rows[0] if rows else payload

    dispatcher_message = (
        f"OKA-RIDES: New ride {created.get('reference', reference)}. "
        f"Customer: {created.get('name', customer.get('full_name', 'Customer'))}. "
        f"Pickup: {created.get('pickup', ride.pickup)}. "
        f"Destination: {created.get('destination', ride.destination)}. "
        f"Pickup time: {created.get('pickup_timing', ride.pickupTiming)}. "
        "Open the dispatcher dashboard to review and dispatch."
    )
    notification = await send_dispatcher_sms(dispatcher_message)

    return {
        "message": "Ride request created",
        "reference": created.get("reference", reference),
        "status": created.get("status", "REQUESTED"),
        "supabaseRecordId": created.get("id"),
        "dispatcherNotification": notification,
    }


@app.get("/api/customer/auth/session")
async def customer_session(customer_id: str = Depends(require_customer_session)):
    customer = await get_customer_record(customer_id)
    return {
        "authenticated": True,
        "customer": {
            "id": customer.get("id"),
            "fullName": customer.get("full_name"),
            "phone": customer.get("phone"),
            "email": customer.get("email"),
        },
    }


@app.patch("/api/customer/profile")
async def customer_profile_update(profile: CustomerProfileUpdate, customer_id: str = Depends(require_customer_session)):
    customer = await get_customer_record(customer_id)
    phone = profile.phone.strip()
    full_name = profile.fullName.strip()
    current_phone = str(customer.get("phone") or "").strip()
    if not full_name:
        raise HTTPException(status_code=422, detail="Full name is required.")
    if phone != current_phone:
        password_hash = customer.get("password_hash")
        if password_hash:
            if not profile.currentPassword or not verify_password(profile.currentPassword, password_hash):
                raise HTTPException(status_code=403, detail="Re-authentication is required before changing your phone number.")
        else:
            raise HTTPException(status_code=403, detail="Google accounts must re-authenticate with Google before changing the phone number.")
    existing = await find_customer_by_phone(phone)
    if existing and str(existing.get("id")) != str(customer_id):
        raise HTTPException(status_code=409, detail="That phone number is already linked to another account.")
    rows = await supabase_request("PATCH", SUPABASE_TABLE_CUSTOMERS, params={"id": f"eq.{customer_id}"}, json_body={"full_name": full_name, "phone": phone}, action_description="customer profile update")
    updated = rows[0] if rows else None
    if not updated:
        raise HTTPException(status_code=502, detail="Profile could not be updated.")
    return {
        "authenticated": True,
        "customer": {
            "id": updated.get("id", customer_id),
            "fullName": updated.get("full_name", full_name),
            "phone": updated.get("phone", phone),
            "email": updated.get("email", customer.get("email")),
        },
    }


@app.post("/api/customer/auth/signup", status_code=201)
async def customer_signup(credentials: CustomerSignup, response: Response, request: Request):
    enforce_rate_limit("signup:" + _client_ip(request), SIGNUP_RATE_LIMIT_MAX, ACTION_RATE_LIMIT_WINDOW, "Too many account creation attempts. Please try again later.")
    email = credentials.email.strip().lower()
    phone = credentials.phone.strip()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    if await find_customer_by_email(email):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    if await find_customer_by_phone(phone):
        raise HTTPException(status_code=409, detail="An account with this phone number already exists.")

    payload = {
        "full_name": credentials.fullName.strip(),
        "phone": phone,
        "email": email,
        "password_hash": hash_password(credentials.password),
    }
    rows = await supabase_request(
        "POST", SUPABASE_TABLE_CUSTOMERS,
        json_body=payload,
        action_description="customer account creation",
    )
    customer = rows[0] if rows else None
    if not customer:
        raise HTTPException(status_code=502, detail="Customer account could not be created.")

    response.set_cookie(
        key=CUSTOMER_SESSION_COOKIE,
        value=create_session(str(customer["id"]), customer_session_secret(), CUSTOMER_SESSION_TTL),
        max_age=CUSTOMER_SESSION_TTL,
        httponly=True,        secure=True,
        samesite="lax",
        path="/api",
    )
    return {
        "authenticated": True,
        "customer": {
            "id": customer.get("id"),
            "fullName": customer.get("full_name"),
            "phone": customer.get("phone"),
            "email": customer.get("email"),
        },
    }


@app.post("/api/customer/auth/login")
async def customer_login(credentials: CustomerLogin, response: Response, request: Request):
    enforce_auth_rate_limit(request)
    email = credentials.email.strip().lower()
    customer = await find_customer_by_email(email)
    if not customer or not verify_password(credentials.password, customer.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    response.set_cookie(
        key=CUSTOMER_SESSION_COOKIE,
        value=create_session(str(customer["id"]), customer_session_secret(), CUSTOMER_SESSION_TTL),
        max_age=CUSTOMER_SESSION_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api",
    )
    return {
        "authenticated": True,
        "customer": {
            "id": customer.get("id"),
            "fullName": customer.get("full_name"),
            "phone": customer.get("phone"),
            "email": customer.get("email"),
        },
    }


@app.post("/api/customer/auth/logout")
async def customer_logout(response: Response):
    response.delete_cookie(
        key=CUSTOMER_SESSION_COOKIE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api",
    )
    return {"authenticated": False}


@app.post("/api/customer/auth/google")
async def customer_google_login(credentials: CustomerGoogleLogin, response: Response, request: Request):
    enforce_auth_rate_limit(request)
    url, service_key = supabase_settings()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            auth_response = await client.get(
                f"{url}/auth/v1/user",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {credentials.accessToken}",
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to verify Google sign-in.") from exc

    if auth_response.is_error:
        raise HTTPException(status_code=401, detail="Google sign-in could not be verified.")

    try:
        auth_user = auth_response.json()
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Google sign-in returned an invalid response.") from exc

    email = str(auth_user.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="Google did not provide an email address.")

    customer = await find_customer_by_email(email)
    if not customer:
        phone = credentials.phone.strip()
        if not phone:
            raise HTTPException(status_code=422, detail="Phone number is required for a new Google account.")
        if await find_customer_by_phone(phone):
            raise HTTPException(status_code=409, detail="That phone number is already linked to another account.")
        metadata = auth_user.get("user_metadata") or {}
        full_name = (
            str(metadata.get("full_name") or metadata.get("name") or "").strip()
            or email.split("@", 1)[0]
        )
        rows = await supabase_request(
            "POST",
            SUPABASE_TABLE_CUSTOMERS,
            json_body={
                "full_name": full_name,
                "phone": phone,
                "email": email,
                "password_hash": None,
            },
            action_description="Google customer account creation",
        )
        customer = rows[0] if rows else None
        if not customer:
            raise HTTPException(status_code=502, detail="Google customer account could not be created.")

    response.set_cookie(
        key=CUSTOMER_SESSION_COOKIE,
        value=create_session(str(customer["id"]), customer_session_secret(), CUSTOMER_SESSION_TTL),
        max_age=CUSTOMER_SESSION_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api",
    )
    return {
        "authenticated": True,
        "customer": {
            "id": customer.get("id"),
            "fullName": customer.get("full_name"),
            "phone": customer.get("phone"),
            "email": customer.get("email"),
        },
    }


@app.get("/api/customer/rides")
async def customer_rides(customer_id: str = Depends(require_customer_session)):
    rows = await supabase_request(
        "GET", SUPABASE_TABLE_RIDES,
        params={
            "select": "*",
            "customer_id": f"eq.{customer_id}",
            "order": "created_at.desc",
            "limit": "100",
        },
        action_description="customer ride history",
    )
    return {"rides": [ride_representation(row) for row in rows]}


@app.get("/api/customer/rides/{reference}")
async def customer_ride(
    reference: str = Path(min_length=1),
    customer_id: str = Depends(require_customer_session),
):
    record = await get_customer_ride(reference, customer_id)
    status = record.get("status")
    response = {
        "reference": record.get("reference"),
        "status": status,
        "fare": record.get("fare"),
        "fareConfirmed": record.get("fare_confirmed", False),
        "paymentStatus": record.get("payment_status"),
        "driverAssigned": status in {"DRIVER ASSIGNED", "PAID", "PICKED UP", "COMPLETED"},
    }
    if response["driverAssigned"]:
        response.update({
            "driverName": record.get("driver_name"),
            "driverPhone": record.get("driver_phone"),
            "driverVehicle": record.get("driver_vehicle"),
            "driverPlateNumber": record.get("driver_plate_number"),
        })
    return response


@app.post("/api/payments/paystack/initialize")
async def initialize_paystack_payment(
    request: Request,
    payment: PaystackInitialize,
    customer_id: str = Depends(require_customer_session),
):
    enforce_rate_limit("payment:" + str(customer_id), PAYMENT_RATE_LIMIT_MAX, ACTION_RATE_LIMIT_WINDOW, "Too many payment attempts. Please try again later.")
    secret = paystack_secret()
    record = await get_customer_ride(payment.reference, customer_id)

    if (
        record.get("status") != "DRIVER ASSIGNED"
        or record.get("fare_confirmed") is not True
    ):
        raise HTTPException(
            status_code=409,
            detail="Payment is only available after a driver is assigned and the fare is confirmed.",
        )

    if record.get("payment_status") == "PAID":
        raise HTTPException(status_code=409, detail="This ride has already been paid.")

    fare = record.get("fare")
    try:
        amount_kobo = naira_to_kobo(float(fare))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="This ride does not have a valid confirmed fare.",
        ) from exc

    safe_reference = (
        "OKARIDE-"
        + "".join(ch for ch in payment.reference if ch.isalnum() or ch in "-.=")
        + "-"
        + secrets.token_hex(4)
    )

    payload = {
        "email": str(record.get("email") or payment.email).strip(),
        "amount": str(amount_kobo),
        "currency": "NGN",
        "reference": safe_reference,
        "callback_url": paystack_callback_url(),
        "metadata": {"ride_reference": payment.reference},
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{PAYSTACK_API_BASE}/transaction/initialize",
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to initialize payment with Paystack.",
        ) from exc

    try:
        response_payload = response.json()
    except ValueError:
        response_payload = {}

    if response.is_error or response_payload.get("status") is not True:
        raise HTTPException(
            status_code=502,
            detail="Paystack could not initialize the payment.",
        )

    data = response_payload.get("data") or {}
    return {
        "authorizationUrl": data.get("authorization_url"),
        "accessCode": data.get("access_code"),
        "reference": data.get("reference"),
    }


@app.get("/api/payments/paystack/callback")
async def paystack_callback(reference: str = Query(min_length=1)):
    secret = paystack_secret()
    data = await fetch_paystack_transaction(reference, secret)
    ride_reference = (data.get("metadata") or {}).get("ride_reference")

    if not ride_reference:
        raise HTTPException(
            status_code=400,
            detail="Payment is missing ride metadata.",
        )

    record = await find_ride_by_reference(ride_reference)
    fare = record.get("fare")
    if not fare:
        raise HTTPException(status_code=409, detail="Ride fare is unavailable.")

    verified = await verify_paystack_transaction(
        reference,
        naira_to_kobo(float(fare)),
        secret,
    )
    await mark_paystack_paid(record, verified)

    frontend = os.getenv("FRONTEND_ORIGIN", "").split(",")[0].strip()
    if frontend:
        return RedirectResponse(
            url=f"{frontend.rstrip('/')}/success.html?payment=success&reference={reference}"
        )

    return {"success": True, "reference": reference}


@app.post("/api/payments/paystack/webhook")
async def paystack_webhook(request: Request):
    secret = paystack_secret()
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature")

    if not paystack_signature_is_valid(raw_body, signature, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload.") from exc

    if event.get("event") != "charge.success":
        return {"received": True}

    data = event.get("data") or {}
    reference = data.get("reference")
    ride_reference = (data.get("metadata") or {}).get("ride_reference")

    if not reference or not ride_reference:
        return {"received": True}

    record = await find_ride_by_reference(ride_reference)
    fare = record.get("fare")
    if not fare:
        raise HTTPException(status_code=409, detail="Ride fare is unavailable.")

    verified = await verify_paystack_transaction(
        reference,
        naira_to_kobo(float(fare)),
        secret,
    )
    await mark_paystack_paid(record, verified)

    return {"received": True, "processed": True}


@app.get("/api/rides")
async def list_rides(
    status: RideListStatus | None = Query(default=None),
    _: str = Depends(require_dispatcher_session),
):
    params = {
        "select": "*",
        "order": "created_at.desc",
        "limit": "100",
    }
    if status is not None:
        params["status"] = f"eq.{status}"

    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_RIDES,
        params=params,
        action_description="ride list",
    )
    return {"rides": [ride_representation(row) for row in rows]}


@app.get("/api/rides/{ride_id}")
async def get_ride(
    ride_id: str = Path(min_length=1),
    _: str = Depends(require_dispatcher_session),
):
    record = await get_ride_record(ride_id)
    return ride_representation(record)


@app.get("/api/drivers")
async def list_drivers(_: str = Depends(require_dispatcher_session)):
    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_DRIVERS,
        params={
            "select": "*",
            "order": "completed_rides.desc,cancelled_rides.asc",
            "limit": "100",
        },
        action_description="driver list",
    )

    drivers = []
    for row in rows:
        status = row.get("status", "INACTIVE")
        verification_status = row.get("verification_status", "PENDING")
        eligible = status == "ACTIVE" and verification_status == "VERIFIED"
        drivers.append(
            {
                "id": row.get("driver_id"),
                "driverId": row.get("driver_id"),
                "name": row.get("name"),
                "phone": row.get("phone"),
                "vehicle": row.get("vehicle"),
                "plateNumber": row.get("plate_number"),                "status": status,
                "verificationStatus": verification_status,
                "completedRides": row.get("completed_rides", 0),
                "cancelledRides": row.get("cancelled_rides", 0),
                "notes": row.get("notes"),
                "availabilityStatus": row.get("availability_status", "OFFLINE"),
                "currentLatitude": row.get("current_latitude"),
                "currentLongitude": row.get("current_longitude"),
                "lastLocationAt": row.get("last_location_at"),
                "eligible": eligible,
            }
        )

    drivers.sort(
        key=lambda driver: (
            not driver["eligible"],
            -driver["completedRides"],
            driver["cancelledRides"],
        )
    )
    return {"drivers": drivers}


@app.post("/api/drivers", status_code=201)
async def create_driver(
    driver: DriverCreate,
    _: str = Depends(require_dispatcher_session),
):
    payload = {
        "driver_id": driver.driverId,
        "name": driver.name,
        "phone": driver.phone,
        "vehicle": driver.vehicle,
        "plate_number": driver.plateNumber,
        "status": driver.status,
        "verification_status": driver.verificationStatus,
        "completed_rides": driver.completedRides,
        "cancelled_rides": driver.cancelledRides,
        "notes": driver.notes,
    }

    rows = await supabase_request(
        "POST",
        SUPABASE_TABLE_DRIVERS,
        json_body=payload,
        action_description="driver request",
    )
    created = rows[0] if rows else payload
    return {
        "message": "Driver created",
        "driverId": created.get("driver_id", driver.driverId),
        "supabaseRecordId": created.get("id"),
    }


@app.patch("/api/drivers/{driver_id}")
async def update_driver(
    driver: DriverUpdate,
    driver_id: str = Path(min_length=1),
    _: str = Depends(require_dispatcher_session),
):
    fields = {}
    if driver.status is not None:
        fields["status"] = driver.status
    if driver.verificationStatus is not None:
        fields["verification_status"] = driver.verificationStatus
    if driver.availabilityStatus is not None:
        if driver.availabilityStatus == "ONLINE" and (
            driver.status == "INACTIVE" or driver.verificationStatus == "REJECTED"
        ):
            raise HTTPException(
                status_code=409,
                detail="Only ACTIVE and VERIFIED drivers can go ONLINE.",
            )
        fields["availability_status"] = driver.availabilityStatus
    if driver.currentLatitude is not None:
        fields["current_latitude"] = driver.currentLatitude
    if driver.currentLongitude is not None:
        fields["current_longitude"] = driver.currentLongitude
    if driver.notes is not None:
        fields["notes"] = driver.notes

    if not fields:
        raise HTTPException(
            status_code=422,
            detail="At least one driver field must be provided.",
        )

    existing_driver = await get_driver_record(driver_id)
    effective_status = driver.status if driver.status is not None else existing_driver.get("status")
    effective_verification = (
        driver.verificationStatus
        if driver.verificationStatus is not None
        else existing_driver.get("verification_status")
    )
    if driver.availabilityStatus == "ONLINE" and (
        effective_status != "ACTIVE" or effective_verification != "VERIFIED"
    ):
        raise HTTPException(
            status_code=409,
            detail="Only ACTIVE and VERIFIED drivers can go ONLINE.",
        )

    rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_DRIVERS,
        params={"driver_id": f"eq.{driver_id}"},
        json_body=fields,
        action_description="driver update",
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Driver not found.")
    return {"message": "Driver updated", "driverId": driver_id}


@app.get("/api/rides/{ride_id}/dispatch-options")
async def dispatch_options(
    ride_id: str = Path(min_length=1),
    _: str = Depends(require_dispatcher_session),
):
    """
    Human-in-the-loop dispatch.

    The API ranks suitable drivers using live availability and, when both
    ride and driver coordinates exist, straight-line distance. It NEVER
    assigns a driver automatically. The dispatcher chooses a driver and
    calls /assign.
    """
    ride = await get_ride_record(ride_id)
    if ride.get("status") != "REQUESTED":
        raise HTTPException(
            status_code=409,
            detail="Dispatch options are only available for REQUESTED rides.",
        )

    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_DRIVERS,
        params={"select": "*", "limit": "100"},
        action_description="dispatch driver lookup",
    )

    def distance_km(lat1, lon1, lat2, lon2):
        if None in (lat1, lon1, lat2, lon2):
            return None
        from math import asin, cos, radians, sin, sqrt
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return 6371.0 * 2 * asin(sqrt(a))

    candidates = []
    for row in rows:
        active_verified = (
            row.get("status") == "ACTIVE"
            and row.get("verification_status") == "VERIFIED"
        )
        online = row.get("availability_status", "OFFLINE") == "ONLINE"
        distance = distance_km(
            ride.get("pickup_latitude"),
            ride.get("pickup_longitude"),
            row.get("current_latitude"),
            row.get("current_longitude"),
        )
        candidates.append({
            "driverId": row.get("driver_id"),
            "name": row.get("name"),
            "phone": row.get("phone"),
            "vehicle": row.get("vehicle"),
            "plateNumber": row.get("plate_number"),
            "status": row.get("status"),
            "verificationStatus": row.get("verification_status"),
            "availabilityStatus": row.get("availability_status", "OFFLINE"),
            "distanceKm": round(distance, 2) if distance is not None else None,
            "completedRides": row.get("completed_rides", 0),
            "cancelledRides": row.get("cancelled_rides", 0),
            "canAssign": active_verified and online,
        })

    candidates.sort(
        key=lambda item: (
            not item["canAssign"],
            item["distanceKm"] is None,
            item["distanceKm"] if item["distanceKm"] is not None else float("inf"),
            -item["completedRides"],
            item["cancelledRides"],
        )
    )

    return {
        "ride": {
            "id": ride.get("id"),
            "reference": ride.get("reference"),
            "pickup": ride.get("pickup"),
            "destination": ride.get("destination"),
            "pickupLatitude": ride.get("pickup_latitude"),
            "pickupLongitude": ride.get("pickup_longitude"),
        },
        "dispatchMode": "MANUAL",
        "message": "Select a driver to dispatch. No driver is assigned automatically.",
        "drivers": candidates,
    }


@app.post("/api/rides/{ride_id}/assign")
async def assign_driver(
    driver_id: str,
    ride_id: str = Path(min_length=1),
    _: str = Depends(require_dispatcher_session),
):
    driver = await get_driver_record(driver_id)

    if (
        driver.get("status") != "ACTIVE"
        or driver.get("verification_status") != "VERIFIED"
        or driver.get("availability_status", "OFFLINE") != "ONLINE"
    ):
        raise HTTPException(
            status_code=409,
            detail="Driver must be ACTIVE, VERIFIED, and ONLINE before dispatch.",
        )

    ride = await get_ride_record(ride_id)
    if ride.get("status") != "REQUESTED":
        raise HTTPException(
            status_code=409,
            detail="Only REQUESTED rides can be assigned.",
        )

    fields = {
        "status": "DRIVER ASSIGNED",
        "driver_id": driver.get("driver_id"),
        "driver_name": driver.get("name"),
        "driver_phone": driver.get("phone"),
        "driver_vehicle": driver.get("vehicle"),
        "driver_plate_number": driver.get("plate_number"),
    }

    await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{ride_id}"},
        json_body=fields,
        action_description="ride assignment",
    )

    await supabase_request(
        "PATCH",
        SUPABASE_TABLE_DRIVERS,
        params={"driver_id": f"eq.{driver_id}"},
        json_body={"availability_status": "BUSY"},
        action_description="driver availability update",
    )

    await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{ride_id}"},
        json_body={
            "dispatch_attempts": (ride.get("dispatch_attempts") or 0) + 1,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        },
        action_description="dispatch audit update",
    )

    ride_after = {**ride, **fields}
    sms_result = await notify_driver_of_assignment(ride_after, driver)

    return {
        "message": "Driver assigned",
        "rideId": ride_id,
        "driverId": driver.get("driver_id"),
        "status": "DRIVER ASSIGNED",
        "smsNotification": sms_result,
    }


@app.patch("/api/rides/{ride_id}/fare")
async def confirm_fare(
    fare_update: FareUpdate,
    ride_id: str = Path(min_length=1),
    _: str = Depends(require_dispatcher_session),
):
    ride = await get_ride_record(ride_id)

    if ride.get("status") != "DRIVER ASSIGNED":
        raise HTTPException(
            status_code=409,
            detail="Fare can only be confirmed for DRIVER ASSIGNED rides.",
        )

    if not fare_update.fareConfirmed:
        raise HTTPException(
            status_code=422,
            detail="Fare must be confirmed before payment can proceed.",
        )

    await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,        params={"id": f"eq.{ride_id}"},
        json_body={
            "fare": fare_update.fare,
            "fare_confirmed": True,
            "payment_status": "PENDING",
        },
        action_description="fare confirmation",
    )

    return {
        "success": True,
        "rideId": ride_id,
        "fare": fare_update.fare,
        "fareConfirmed": True,
        "paymentStatus": "PENDING",
    }


@app.patch("/api/rides/{ride_id}/payment")
async def verify_payment(
    payment: PaymentUpdate,
    ride_id: str = Path(min_length=1),
    _: str = Depends(require_dispatcher_session),
):
    ride = await get_ride_record(ride_id)

    if ride.get("status") != "DRIVER ASSIGNED":
        raise HTTPException(
            status_code=409,
            detail="Payment can only be verified for DRIVER ASSIGNED rides.",
        )

    if not ride.get("fare") or ride.get("fare_confirmed") is not True:
        raise HTTPException(
            status_code=409,
            detail="Payment requires a confirmed fare.",
        )

    if payment.paymentStatus == "PAID" and not payment.paymentReference.strip():
        raise HTTPException(
            status_code=422,
            detail="Payment reference is required for PAID payments.",
        )

    fields = {"payment_status": payment.paymentStatus}
    if payment.paymentStatus == "PAID":
        fields.update(
            {
                "payment_reference": payment.paymentReference.strip(),
                "payment_method": payment.paymentMethod,
                "status": "PAID",
            }
        )
    else:
        fields.update(
            {
                "payment_reference": None,
                "payment_method": None,
            }
        )

    await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{ride_id}"},
        json_body=fields,
        action_description="payment update",
    )

    return {
        "success": True,
        "rideId": ride_id,
        "paymentStatus": payment.paymentStatus,
        "rideStatus": "PAID" if payment.paymentStatus == "PAID" else "DRIVER ASSIGNED",
    }


@app.patch("/api/rides/{ride_id}/status")
async def update_ride_status(
    status_update: RideStatusUpdate,
    ride_id: str = Path(min_length=1),
    _: str = Depends(require_dispatcher_session),
):
    ride = await get_ride_record(ride_id)
    current_status = ride.get("status")

    allowed_transitions = {
        "PAID": {"PICKED UP", "CANCELLED"},
        "PICKED UP": {"COMPLETED", "CANCELLED"},
        "REQUESTED": {"CANCELLED"},
        "DRIVER ASSIGNED": {"CANCELLED"},
    }

    if status_update.status not in allowed_transitions.get(current_status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition ride from {current_status or 'unknown'} "
            f"to {status_update.status}.",
        )

    assigned_driver_id = ride.get("driver_id")
    changes_driver_counter = status_update.status in {"COMPLETED", "CANCELLED"}
    is_requested_cancellation = (
        status_update.status == "CANCELLED" and current_status == "REQUESTED"
    )

    if changes_driver_counter and not is_requested_cancellation:
        if not assigned_driver_id:
            raise HTTPException(
                status_code=409,
                detail="This transition requires an assigned driver.",
            )

        driver = await get_driver_record(assigned_driver_id)
        counter_field = (
            "completed_rides"
            if status_update.status == "COMPLETED"
            else "cancelled_rides"
        )
        old_counter = driver.get(counter_field, 0)

        if (
            not isinstance(old_counter, int)
            or isinstance(old_counter, bool)
            or old_counter < 0
        ):
            raise HTTPException(
                status_code=502,
                detail=f"Assigned driver's {counter_field} value is invalid.",
            )

        driver_changes = {
            counter_field: old_counter + 1,
            "availability_status": "OFFLINE" if status_update.status == "CANCELLED" else "ONLINE",
        }
        await supabase_request(
            "PATCH",
            SUPABASE_TABLE_DRIVERS,
            params={"driver_id": f"eq.{assigned_driver_id}"},
            json_body=driver_changes,
            action_description=f"driver {counter_field} update",
        )

    await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{ride_id}"},
        json_body={"status": status_update.status},
        action_description="ride status update",
    )

    return {
        "success": True,
        "rideId": ride_id,
        "previousStatus": current_status,
        "status": status_update.status,
    }