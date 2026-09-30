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
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Path, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

load_dotenv()

SUPABASE_TABLE_RIDES = "oka_rides_rides"
SUPABASE_TABLE_DRIVERS = "oka_rides_drivers"
SUPABASE_TABLE_CUSTOMERS = "oka_rides_customers"
SUPABASE_TABLE_AUDIT_LOGS = "oka_rides_audit_logs"
SUPABASE_TABLE_CUSTOMER_SESSIONS = "oka_rides_customer_sessions"
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
        "https://oka-rides.vercel.app",
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/customer/auth/login",
    "/api/customer/auth/signup",
    "/api/customer/auth/google",
    "/api/payments/paystack/webhook",
}


def request_origin_is_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin:
        return origin in frontend_origins
    referer = request.headers.get("referer")
    if referer:
        from urllib.parse import urlparse
        parsed = urlparse(referer)
        return f"{parsed.scheme}://{parsed.netloc}" in frontend_origins
    return False


def csrf_secret() -> str:
    secret = os.getenv("CSRF_SECRET") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not secret:
        raise HTTPException(status_code=500, detail="CSRF security is not configured on the server.")
    return secret


def create_csrf_token(session_cookie: str) -> str:
    nonce = secrets.token_urlsafe(32)
    message = f"{len(session_cookie)}!{session_cookie}!{len(nonce)}!{nonce}"
    signature = hmac.new(
        csrf_secret().encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{signature}.{nonce}"


def validate_csrf_token(session_cookie: str, supplied_token: str | None):
    if not supplied_token or "." not in supplied_token:
        raise HTTPException(status_code=403, detail="CSRF token required.")
    signature, nonce = supplied_token.split(".", 1)
    if not signature or not nonce:
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")
    message = f"{len(session_cookie)}!{session_cookie}!{len(nonce)}!{nonce}"
    expected = hmac.new(
        csrf_secret().encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")


@app.middleware("http")
async def csrf_protection_middleware(request: Request, call_next):
    if request.method.upper() in SAFE_HTTP_METHODS or not request.url.path.startswith("/api/"):
        return await call_next(request)
    if request.url.path == "/api/payments/paystack/webhook":
        return await call_next(request)
    if not request_origin_is_allowed(request):
        raise HTTPException(status_code=403, detail="Untrusted request origin.")
    if request.url.path in CSRF_EXEMPT_PATHS:
        return await call_next(request)

    customer_cookie = request.cookies.get(CUSTOMER_SESSION_COOKIE)
    dispatcher_cookie = request.cookies.get(DISPATCHER_SESSION_COOKIE)
    csrf_header = request.headers.get("X-CSRF-Token")

    if customer_cookie:
        validate_csrf_token(customer_cookie, csrf_header)
    elif dispatcher_cookie:
        username, _, session_secret = dispatcher_settings()
        if session_username(dispatcher_cookie, session_secret) != username:
            raise HTTPException(status_code=401, detail="Dispatcher authentication required.")
        validate_csrf_token(dispatcher_cookie, csrf_header)

    return await call_next(request)


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
    reauth: str = Field(default="", max_length=128)


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


async def supabase_rpc(function_name: str, json_body: dict) -> list:
    url, service_key = supabase_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{url}/rest/v1/rpc/{function_name}",
                headers=supabase_headers(service_key),
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach the rate-limit service.") from exc

    if response.is_error:
        raise HTTPException(status_code=502, detail="Rate-limit service rejected the request.")

    if not response.content:
        return []
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Rate-limit service returned an invalid response.") from exc
    return payload if isinstance(payload, list) else [payload]


async def enforce_shared_rate_limit(key: str, maximum: int, window: int, detail: str):
    rows = await supabase_rpc(
        "oka_rides_check_rate_limit",
        {
            "p_rate_key": key,
            "p_limit": maximum,
            "p_window_seconds": window,
        },
    )
    result = rows[0] if rows else {}
    if not result.get("allowed", False):
        retry_after = max(1, int(result.get("retry_after") or window))
        raise HTTPException(
            status_code=429,
            detail=detail,
            headers={"Retry-After": str(retry_after)},
        )


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
    actual_digest = hashlib.pbkdf2_hmac(        "sha256",
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


def hash_customer_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_customer_session(customer_id: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf_token = create_csrf_token(token)
    await supabase_request(
        "POST",
        SUPABASE_TABLE_CUSTOMER_SESSIONS,
        json_body={
            "customer_id": str(customer_id),
            "token_hash": hash_customer_session_token(token),
            "expires_at": datetime.fromtimestamp(time.time() + CUSTOMER_SESSION_TTL, tz=timezone.utc).isoformat(),
        },
        action_description="customer session creation",
    )
    return token, csrf_token


async def revoke_customer_session(token: str | None):
    if not token:
        return
    await supabase_request(
        "PATCH",
        SUPABASE_TABLE_CUSTOMER_SESSIONS,
        params={
            "token_hash": f"eq.{hash_customer_session_token(token)}",
            "revoked_at": "is.null",
        },
        json_body={"revoked_at": datetime.now(timezone.utc).isoformat()},
        action_description="customer session revocation",
    )


async def require_customer_session(
    session_cookie: str | None = Cookie(
        default=None,
        alias=CUSTOMER_SESSION_COOKIE,
    ),
):
    if not session_cookie:
        raise HTTPException(status_code=401, detail="Customer login required.")

    token_hash = hash_customer_session_token(session_cookie)
    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_CUSTOMER_SESSIONS,
        params={
            "select": "customer_id,expires_at",
            "token_hash": f"eq.{token_hash}",
            "revoked_at": "is.null",
            "expires_at": f"gt.{datetime.now(timezone.utc).isoformat()}",
            "limit": "1",
        },
        action_description="customer session validation",
    )
    if not rows:
        raise HTTPException(status_code=401, detail="Customer login required.")

    return str(rows[0]["customer_id"])


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


async def write_audit_log(actor_type: str, actor_id: str, action: str, entity_type: str, entity_id: str | None = None, metadata: dict | None = None):
    try:
        await supabase_request("POST", SUPABASE_TABLE_AUDIT_LOGS, json_body={
            "actor_type": actor_type,
            "actor_id": str(actor_id),
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id) if entity_id is not None else None,
            "metadata": metadata or {},
        }, action_description="audit log write")
    except HTTPException:
        pass

def ride_representation(record: dict) -> dict:
    return {        "id": record.get("id"),
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
    await enforce_shared_rate_limit("dispatcher-login:ip:" + _client_ip(request), 10, 10 * 60, "Too many authentication attempts. Please try again later.")
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
async def dispatcher_session(request: Request):
    session_cookie = request.cookies.get(DISPATCHER_SESSION_COOKIE)
    username, _, session_secret = dispatcher_settings()
    if not session_cookie or session_username(session_cookie, session_secret) != username:
        raise HTTPException(status_code=401, detail="Dispatcher authentication required.")
    return {"authenticated": True, "csrfToken": create_csrf_token(session_cookie)}


@app.post("/api/auth/logout")
async def dispatcher_logout(response: Response):
    response.delete_cookie(        key=DISPATCHER_SESSION_COOKIE,
        httponly=True,
        secure=os.getenv("DISPATCHER_COOKIE_SECURE", "true").lower() == "true",
        samesite="strict",        path="/api",
    )
    return {"authenticated": False}


@app.post("/api/rides", status_code=201)
async def create_ride(
    request: Request,
    ride: RideRequest,
    customer_id: str = Depends(require_customer_session),
):
    await enforce_shared_rate_limit("customer:" + str(customer_id) + ":ride-create", RIDE_RATE_LIMIT_MAX, ACTION_RATE_LIMIT_WINDOW, "Too many ride requests. Please try again later.")

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
async def customer_session(request: Request, customer_id: str = Depends(require_customer_session)):
    await enforce_shared_rate_limit("customer:" + str(customer_id) + ":session-check", 120, 60, "Too many requests. Please try again later.")
    customer = await get_customer_record(customer_id)
    session_cookie = request.cookies.get(CUSTOMER_SESSION_COOKIE)
    if not session_cookie:
        raise HTTPException(status_code=401, detail="Customer login required.")
    return {
        "authenticated": True,
        "customer": {
            "id": customer.get("id"),
            "fullName": customer.get("full_name"),
            "phone": customer.get("phone"),
            "email": customer.get("email"),
        },
        "csrfToken": create_csrf_token(session_cookie),
    }


@app.patch("/api/customer/profile")
async def customer_profile_update(profile: CustomerProfileUpdate, customer_id: str = Depends(require_customer_session)):
    await enforce_shared_rate_limit("customer:" + str(customer_id) + ":profile-update", 10, ACTION_RATE_LIMIT_WINDOW, "Too many profile changes. Please try again later.")
    customer = await get_customer_record(customer_id)
    phone = profile.phone.strip()
    full_name = profile.fullName.strip()
    current_phone = str(customer.get("phone") or "").strip()
    if not full_name:
        raise HTTPException(status_code=422, detail="Full name is required.")
    if phone != current_phone:
        auth_customer = await find_customer_by_email(str(customer.get("email") or ""))
        password_hash = auth_customer.get("password_hash") if auth_customer else None
        if password_hash:
            if not profile.reauth or not verify_password(profile.reauth, password_hash):
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
    await write_audit_log(
        "customer",
        customer_id,
        "customer.profile_updated",
        "customer",
        customer_id,
        {"phone_changed": phone != current_phone},
    )
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
    await enforce_shared_rate_limit("signup:ip:" + _client_ip(request), SIGNUP_RATE_LIMIT_MAX, 60 * 60, "Too many account creation attempts. Please try again later.")
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

    session_token, csrf_token = await create_customer_session(str(customer["id"]))
    response.delete_cookie(
        key=CUSTOMER_SESSION_COOKIE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api",
    )
    response.set_cookie(
        key=CUSTOMER_SESSION_COOKIE,
        value=session_token,
        max_age=CUSTOMER_SESSION_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    await write_audit_log("customer", customer.get("id"), "customer.account_created", "customer", customer.get("id"), {"method": "password"})
    return {
        "authenticated": True,
        "customer": {
            "id": customer.get("id"),
            "fullName": customer.get("full_name"),
            "phone": customer.get("phone"),
            "email": customer.get("email"),
        },
        "csrfToken": csrf_token,
    }


@app.post("/api/customer/auth/login")
async def customer_login(credentials: CustomerLogin, response: Response, request: Request):
    email = credentials.email.strip().lower()
    await enforce_shared_rate_limit("customer-login:ip:" + _client_ip(request), 10, 10 * 60, "Too many authentication attempts. Please try again later.")
    await enforce_shared_rate_limit("customer-login:account:" + email, 5, 15 * 60, "Too many authentication attempts. Please try again later.")
    customer = await find_customer_by_email(email)
    if not customer or not verify_password(credentials.password, customer.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    session_token, csrf_token = await create_customer_session(str(customer["id"]))
    response.delete_cookie(
        key=CUSTOMER_SESSION_COOKIE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api",
    )
    response.set_cookie(
        key=CUSTOMER_SESSION_COOKIE,
        value=session_token,
        max_age=CUSTOMER_SESSION_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    await write_audit_log("customer", customer.get("id"), "customer.login", "customer", customer.get("id"), {"method": "password"})
    return {
        "authenticated": True,
        "customer": {
            "id": customer.get("id"),
            "fullName": customer.get("full_name"),
            "phone": customer.get("phone"),
            "email": customer.get("email"),
        },
        "csrfToken": csrf_token,
    }


@app.post("/api/customer/auth/logout")
async def customer_logout(
    response: Response,
    session_cookie: str | None = Cookie(
        default=None,
        alias=CUSTOMER_SESSION_COOKIE,
    ),
):
    await revoke_customer_session(session_cookie)
    response.delete_cookie(
        key=CUSTOMER_SESSION_COOKIE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
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
    await enforce_shared_rate_limit("customer-google-login:ip:" + _client_ip(request), 10, 10 * 60, "Too many authentication attempts. Please try again later.")
    url, service_key = supabase_settings()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            auth_response = await client.get(
                f"{url}/auth/v1/user",
                headers={                    "apikey": service_key,
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

    session_token, csrf_token = await create_customer_session(str(customer["id"]))
    response.delete_cookie(
        key=CUSTOMER_SESSION_COOKIE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api",
    )
    response.set_cookie(
        key=CUSTOMER_SESSION_COOKIE,
        value=session_token,
        max_age=CUSTOMER_SESSION_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {
        "authenticated": True,
        "customer": {
            "id": customer.get("id"),
            "fullName": customer.get("full_name"),
            "phone": customer.get("phone"),
            "email": customer.get("email"),
        },
        "csrfToken": csrf_token,
    }


@app.get("/api/customer/rides")
async def customer_rides(customer_id: str = Depends(require_customer_session)):
    await enforce_shared_rate_limit("customer:" + str(customer_id) + ":ride-history", 120, 60, "Too many requests. Please try again later.")
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
    await enforce_shared_rate_limit("customer:" + str(customer_id) + ":ride-lookup", 120, 60, "Too many requests. Please try again later.")
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
    await enforce_shared_rate_limit("customer:" + str(customer_id) + ":payment-init", PAYMENT_RATE_LIMIT_MAX, ACTION_RATE_LIMIT_WINDOW, "Too many payment attempts. Please try again later.")
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

    verified = await verify_paystack_transaction(        reference,
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
    await write_audit_log("dispatcher", _, "driver.updated", "driver", driver_id, {"fields": sorted(fields.keys())})
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