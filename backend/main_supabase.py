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
from datetime import datetime, timezone, timedelta
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Path, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

SUPABASE_TABLE_RIDES = "oka_rides_rides"
SUPABASE_TABLE_DRIVERS = "oka_rides_drivers"
SUPABASE_TABLE_CUSTOMERS = "oka_rides_customers"
SUPABASE_TABLE_AUDIT_LOGS = "oka_rides_audit_logs"
SUPABASE_TABLE_CUSTOMER_SESSIONS = "oka_rides_customer_sessions"

app = FastAPI(title="Awka Rides API")

# Authentication rate limiting. V1 uses per-instance memory; configure via env vars.
AUTH_RATE_LIMIT_MAX = int(os.getenv("AUTH_RATE_LIMIT_MAX", "5"))
AUTH_RATE_LIMIT_WINDOW = int(os.getenv("AUTH_RATE_LIMIT_WINDOW", "900"))
SIGNUP_RATE_LIMIT_MAX = int(os.getenv("SIGNUP_RATE_LIMIT_MAX", "5"))
RIDE_RATE_LIMIT_MAX = int(os.getenv("RIDE_RATE_LIMIT_MAX", "5"))
ACTION_RATE_LIMIT_WINDOW = int(os.getenv("ACTION_RATE_LIMIT_WINDOW", "900"))
GLOBAL_API_RATE_LIMIT_MAX = int(os.getenv("GLOBAL_API_RATE_LIMIT_MAX", "120"))
GLOBAL_API_RATE_LIMIT_WINDOW = int(os.getenv("GLOBAL_API_RATE_LIMIT_WINDOW", "60"))
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
    "/api/driver/auth/login",
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
    # Paystack webhooks are server-to-server and are authenticated by their HMAC signature.
    if request.url.path == "/api/payments/paystack/webhook":
        return await call_next(request)
    if not request_origin_is_allowed(request):
        return JSONResponse(status_code=403, content={"detail": "Untrusted request origin."})
    if request.url.path in CSRF_EXEMPT_PATHS:
        return await call_next(request)

    customer_cookie = request.cookies.get(CUSTOMER_SESSION_COOKIE)
    dispatcher_cookie = request.cookies.get(DISPATCHER_SESSION_COOKIE)
    csrf_header = request.headers.get("X-CSRF-Token")

    # Shared ride-management URLs are used by the dispatcher, while customer
    # booking uses POST /api/rides and customer-specific URLs. If both session
    # cookies exist in the same browser, select the session belonging to this
    # route instead of always validating against the customer cookie first.
    path = request.url.path
    dispatcher_route = (
        path.startswith("/api/auth/")
        or path.startswith("/api/rides/")
        or path == "/api/drivers"
        or path.startswith("/api/drivers/")
    )

    if dispatcher_route and dispatcher_cookie:
        username, _, session_secret = dispatcher_settings()
        if session_username(dispatcher_cookie, session_secret) != username:
            return JSONResponse(status_code=401, content={"detail": "Dispatcher authentication required."})
        try:
            validate_csrf_token(dispatcher_cookie, csrf_header)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    elif customer_cookie:
        try:
            validate_csrf_token(customer_cookie, csrf_header)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    elif dispatcher_cookie:
        username, _, session_secret = dispatcher_settings()
        if session_username(dispatcher_cookie, session_secret) != username:
            return JSONResponse(status_code=401, content={"detail": "Dispatcher authentication required."})
        try:
            validate_csrf_token(dispatcher_cookie, csrf_header)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

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
    pin: str = Field(min_length=4, max_length=12)
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
    pin: str | None = Field(default=None, min_length=4, max_length=12)


class DriverLogin(BaseModel):
    driverId: str = Field(min_length=1, max_length=80)
    pin: str = Field(min_length=4, max_length=12)


class FareUpdate(BaseModel):
    fare: float = Field(gt=0)
    fareConfirmed: bool


class PaymentUpdate(BaseModel):
    paymentStatus: Literal["PAID", "FAILED"]
    paymentReference: str = ""

class PaystackVerifyRequest(BaseModel):
    reference: str = Field(min_length=1, max_length=120)


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
DRIVER_TOKEN_TTL = 12 * 60 * 60
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
    prefer: str = "return=representation",
):
    url, service_key = supabase_settings()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                method,
                f"{url}/rest/v1/{table}",
                headers={
                    **supabase_headers(service_key),
                    "Prefer": prefer,
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
        # Include the PostgREST error code in the API response so database
        # schema/constraint failures can be diagnosed without leaking secrets.
        error_code = payload.get("code")
        diagnostic = f"{detail} (Supabase {error_code})" if error_code else str(detail)
        raise HTTPException(status_code=502, detail=diagnostic)

    if not response.content:
        return []
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Supabase returned a non-JSON response during {action_description}.",
        ) from exc
    if not isinstance(payload, (dict, list)):
        raise HTTPException(
            status_code=502,
            detail=f"Supabase returned an unexpected response during {action_description}.",
        )
    return payload


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


def _hash_rate_identifier(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


@app.middleware("http")
async def global_api_rate_limit_middleware(request: Request, call_next):
    if not request.url.path.startswith("/api/") or request.method.upper() == "OPTIONS":
        return await call_next(request)
    try:
        await enforce_shared_rate_limit(
            "global-api:ip:" + _client_ip(request),
            GLOBAL_API_RATE_LIMIT_MAX,
            GLOBAL_API_RATE_LIMIT_WINDOW,
            "Too many API requests. Please try again later.",
        )
    except HTTPException as exc:
        headers = dict(exc.headers or {})
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers,
        )
    return await call_next(request)


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


def driver_session_secret() -> str:
    secret = os.getenv("DRIVER_SESSION_SECRET") or os.getenv("DISPATCHER_SESSION_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="Driver authentication is not configured on the server.")
    return secret


def create_driver_token(driver_id: str) -> str:
    return create_session(driver_id, driver_session_secret(), DRIVER_TOKEN_TTL)


def require_driver_token(
    authorization: str | None = Header(default=None),
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Driver authentication required.")
    driver_id = session_username(authorization[7:].strip(), driver_session_secret())
    if not driver_id:
        raise HTTPException(status_code=401, detail="Invalid or expired driver session.")
    return driver_id


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


async def supabase_auth_request(method: str, path: str, *, json_body: dict | None = None, access_token: str | None = None) -> dict:
    url, service_key = supabase_settings()
    headers = {"apikey": service_key, "Content-Type": "application/json"}
    headers["Authorization"] = f"Bearer {access_token or service_key}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                method,
                f"{url}/auth/v1/{path.lstrip('/')}",
                headers=headers,
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to reach Supabase Auth.") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.is_error:
        message = payload.get("msg") or payload.get("message") or payload.get("error_description") or "Supabase Auth rejected the request."
        raise HTTPException(
            status_code=401 if response.status_code in {400, 401, 403} else 502,
            detail=str(message),
        )
    return payload if isinstance(payload, dict) else {}


async def supabase_auth_create_user(email: str, password: str, full_name: str) -> dict:
    return await supabase_auth_request(
        "POST",
        "admin/users",
        json_body={
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"full_name": full_name},
        },
    )


async def supabase_auth_password_login(email: str, password: str) -> dict:
    return await supabase_auth_request(
        "POST",
        "token?grant_type=password",
        json_body={"email": email, "password": password},
    )


async def supabase_auth_delete_user(user_id: str) -> None:
    try:
        await supabase_auth_request("DELETE", f"admin/users/{user_id}")
    except HTTPException:
        pass


async def customer_auth_session(customer: dict, response: Response, *, method: str):
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
    await write_audit_log(
        "customer",
        customer.get("id"),
        "customer.login",
        "customer",
        customer.get("id"),
        {"method": method},
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


async def revoke_customer_sessions(customer_id: str):
    await supabase_request(
        "PATCH",
        SUPABASE_TABLE_CUSTOMER_SESSIONS,
        params={"customer_id": f"eq.{customer_id}", "revoked_at": "is.null"},
        json_body={"revoked_at": datetime.now(timezone.utc).isoformat()},
        action_description="customer session revocation after password reset",
    )


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
        "paymentPaidAt": record.get("payment_paid_at"),
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
        "dispatchOfferStatus": record.get("dispatch_offer_status", "NONE"),
        "dispatchOfferExpiresAt": record.get("dispatch_offer_expires_at"),
    }


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

    dispatch = await auto_dispatch_ride(created)
    return {
        "message": "Ride request created",
        "reference": created.get("reference", reference),
        "status": created.get("status", "REQUESTED"),
        "supabaseRecordId": created.get("id"),
        "dispatcherNotification": notification,
        "dispatch": dispatch,
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
    full_name = credentials.fullName.strip()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    if await find_customer_by_email(email):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    if await find_customer_by_phone(phone):
        raise HTTPException(status_code=409, detail="An account with this phone number already exists.")

    try:
        auth_user = await supabase_auth_create_user(email, credentials.password, full_name)
    except HTTPException as exc:
        if exc.status_code == 401:
            raise HTTPException(status_code=409, detail="An account with this email already exists.") from exc
        raise

    auth_user_id = str(auth_user.get("id") or (auth_user.get("user") or {}).get("id") or "")
    if not auth_user_id:
        raise HTTPException(status_code=502, detail="Supabase Auth did not return a user id.")

    try:
        rows = await supabase_request(
            "POST",
            SUPABASE_TABLE_CUSTOMERS,
            json_body={
                "full_name": full_name,
                "phone": phone,
                "email": email,
                "password_hash": None,
                "auth_user_id": auth_user_id,
            },
            action_description="customer account creation",
        )
    except HTTPException:
        await supabase_auth_delete_user(auth_user_id)
        raise
    customer = rows[0] if rows else None
    if not customer:
        await supabase_auth_delete_user(auth_user_id)
        raise HTTPException(status_code=502, detail="Customer account could not be created.")

    return await customer_auth_session(customer, response, method="password")


@app.post("/api/customer/auth/login")
async def customer_login(credentials: CustomerLogin, response: Response, request: Request):
    email = credentials.email.strip().lower()
    await enforce_shared_rate_limit("customer-login:ip:" + _client_ip(request), 10, 10 * 60, "Too many authentication attempts. Please try again later.")
    await enforce_shared_rate_limit("customer-login:account:" + email, 5, 15 * 60, "Too many authentication attempts. Please try again later.")
    customer = await find_customer_by_email(email)
    if not customer:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    auth_user_id = customer.get("auth_user_id")
    if not auth_user_id:
        legacy_hash = customer.get("password_hash") or ""
        if not verify_password(credentials.password, legacy_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        auth_user = await supabase_auth_create_user(
            email,
            credentials.password,
            str(customer.get("full_name") or email.split("@", 1)[0]),
        )
        auth_user_id = str(auth_user.get("id") or (auth_user.get("user") or {}).get("id") or "")
        if not auth_user_id:
            raise HTTPException(status_code=502, detail="Supabase Auth did not return a user id.")
        await supabase_request(
            "PATCH",
            SUPABASE_TABLE_CUSTOMERS,
            params={"id": f"eq.{customer['id']}"},
            json_body={"auth_user_id": auth_user_id, "password_hash": None},
            action_description="legacy customer authentication migration",
        )
        customer["auth_user_id"] = auth_user_id
    else:
        await supabase_auth_password_login(email, credentials.password)

    return await customer_auth_session(customer, response, method="password")


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
                headers={"apikey": service_key, "Authorization": f"Bearer {credentials.accessToken}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to verify Google sign-in.") from exc
    if auth_response.is_error:
        raise HTTPException(status_code=401, detail="Google sign-in could not be verified.")
    try:
        auth_user = auth_response.json()
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Google sign-in returned an invalid response.") from exc

    auth_user_id = str(auth_user.get("id") or "").strip()
    email = str(auth_user.get("email") or "").strip().lower()
    if not auth_user_id or not email:
        raise HTTPException(status_code=422, detail="Google did not provide a valid account.")

    customer = await find_customer_by_email(email)
    if not customer:
        phone = credentials.phone.strip()
        if not phone:
            raise HTTPException(status_code=422, detail="Phone number is required for a new Google account.")
        if await find_customer_by_phone(phone):
            raise HTTPException(status_code=409, detail="That phone number is already linked to another account.")
        metadata = auth_user.get("user_metadata") or {}
        full_name = str(metadata.get("full_name") or metadata.get("name") or "").strip() or email.split("@", 1)[0]
        rows = await supabase_request(
            "POST",
            SUPABASE_TABLE_CUSTOMERS,
            json_body={
                "full_name": full_name,
                "phone": phone,
                "email": email,
                "password_hash": None,
                "auth_user_id": auth_user_id,
            },
            action_description="Google customer account creation",
        )
        customer = rows[0] if rows else None
        if not customer:
            raise HTTPException(status_code=502, detail="Google customer account could not be created.")
    elif customer.get("auth_user_id") and str(customer["auth_user_id"]) != auth_user_id:
        raise HTTPException(status_code=409, detail="This email is linked to a different authentication account.")
    elif not customer.get("auth_user_id"):
        await supabase_request(
            "PATCH",
            SUPABASE_TABLE_CUSTOMERS,
            params={"id": f"eq.{customer['id']}"},
            json_body={"auth_user_id": auth_user_id, "password_hash": None},
            action_description="Google customer authentication linking",
        )
        customer["auth_user_id"] = auth_user_id

    return await customer_auth_session(customer, response, method="google")


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
    dispatch_refresh = await refresh_dispatch_offer(record)
    if dispatch_refresh.get("status") != "UNCHANGED":
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


@app.get("/api/payment-records")
async def list_payment_records(_: str = Depends(require_dispatcher_session)):
    rows = await supabase_request("GET", "oka_rides_payment_records",
        params={"select": "*", "order": "created_at.desc", "limit": "500"},
        action_description="payment records list")
    return {"payments": rows}


@app.get("/api/rides/{ride_id}/payment-records")
async def list_ride_payment_records(ride_id: str = Path(min_length=1), _: str = Depends(require_dispatcher_session)):
    await get_ride_record(ride_id)
    rows = await supabase_request("GET", "oka_rides_payment_records",
        params={"select": "*", "ride_id": f"eq.{ride_id}", "order": "created_at.desc", "limit": "100"},
        action_description="ride payment records list")
    return {"payments": rows}


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
            "limit": "1000",
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
    # Account activation is controlled by the backend, not by client-supplied
    # status values. Every newly created driver is active and verified.
    payload = {
        "driver_id": driver.driverId,
        "name": driver.name,
        "phone": driver.phone,
        "vehicle": driver.vehicle,
        "plate_number": driver.plateNumber,
        "status": "ACTIVE",
        "verification_status": "VERIFIED",
        "completed_rides": driver.completedRides,
        "cancelled_rides": driver.cancelledRides,
        "notes": driver.notes,
        "pin_hash": hash_password(driver.pin) if driver.pin else None,
    }

    try:
        rows = await supabase_request(
            "POST",
            SUPABASE_TABLE_DRIVERS,
            json_body=payload,
            action_description="driver request",
        )
        if not isinstance(rows, list):
            raise HTTPException(
                status_code=502,
                detail="Supabase returned an unexpected result while creating the driver.",
            )
        if not rows:
            raise HTTPException(
                status_code=502,
                detail="Supabase did not confirm that the driver record was created.",
            )
        created = rows[0]
        return {
            "message": "Driver created",
            "driverId": created.get("driver_id", driver.driverId),
            "supabaseRecordId": created.get("id"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        # Keep the response actionable while recording the full exception in
        # Vercel runtime logs for diagnosis. Never log the PIN or its hash.
        import logging
        logging.getLogger("oka_rides.driver_create").exception(
            "Unexpected error while creating driver account"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Driver creation failed unexpectedly ({type(exc).__name__}).",
        ) from exc


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
    if driver.pin is not None:
        fields["pin_hash"] = hash_password(driver.pin)

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


async def auto_dispatch_ride(ride: dict) -> dict:
    """Offer a REQUESTED ride to the best currently available driver."""
    if ride.get("status") != "REQUESTED":
        return {"status": "SKIPPED", "reason": "Ride is not REQUESTED."}

    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_DRIVERS,
        params={"select": "*", "limit": "1000"},
        action_description="automatic dispatch driver lookup",
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
        if (
            row.get("status") != "ACTIVE"
            or row.get("verification_status") != "VERIFIED"
            or row.get("availability_status", "OFFLINE") != "ONLINE"
        ):
            continue
        distance = distance_km(
            ride.get("pickup_latitude"),
            ride.get("pickup_longitude"),
            row.get("current_latitude"),
            row.get("current_longitude"),
        )
        candidates.append((distance, row))

    if not candidates:
        return {"status": "WAITING", "reason": "No eligible drivers are online."}

    candidates.sort(
        key=lambda item: (
            item[0] is None,
            item[0] if item[0] is not None else float("inf"),
            -(item[1].get("completed_rides") or 0),
            item[1].get("cancelled_rides") or 0,
        )
    )

    driver = candidates[0][1]
    driver_id = driver.get("driver_id")

    reserved = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_DRIVERS,
        params={"driver_id": f"eq.{driver_id}", "availability_status": "eq.ONLINE"},
        json_body={"availability_status": "BUSY"},
        action_description="automatic dispatch driver reservation",
    )
    if not reserved:
        return {"status": "RETRY", "reason": "Selected driver became unavailable."}

    ride_id = ride.get("id")
    offer_rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={
            "id": f"eq.{ride_id}",
            "status": "eq.REQUESTED",
            "dispatch_offer_status": "eq.NONE",
        },
        json_body={
            "driver_id": driver_id,
            "driver_name": driver.get("name"),
            "driver_phone": driver.get("phone"),
            "driver_vehicle": driver.get("vehicle"),
            "driver_plate_number": driver.get("plate_number"),
            "dispatch_offer_status": "OFFERED",
            "dispatch_offer_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
            "dispatch_attempts": (ride.get("dispatch_attempts") or 0) + 1,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        },
        action_description="automatic ride offer",
    )

    if not offer_rows:
        await supabase_request(
            "PATCH",
            SUPABASE_TABLE_DRIVERS,
            params={"driver_id": f"eq.{driver_id}", "availability_status": "eq.BUSY"},
            json_body={"availability_status": "ONLINE"},
            action_description="automatic dispatch reservation rollback",
        )
        return {"status": "RETRY", "reason": "Ride was already offered or changed."}

    offered = {**ride, **offer_rows[0]}
    notification = await notify_driver_of_assignment(offered, driver)
    await write_audit_log(
        "system",
        "automatic-dispatch",
        "ride.driver_offered",
        "ride",
        ride_id,
        {"driver_id": driver_id},
    )
    return {
        "status": "OFFERED",
        "rideId": ride_id,
        "driverId": driver_id,
        "expiresAt": offered.get("dispatch_offer_expires_at"),
        "notification": notification,
    }


async def refresh_dispatch_offer(record: dict) -> dict:
    if record.get("status") != "REQUESTED" or record.get("dispatch_offer_status") != "OFFERED":
        return {"status": "UNCHANGED"}

    expires = record.get("dispatch_offer_expires_at")
    if not expires:
        return {"status": "UNCHANGED"}

    try:
        expired = datetime.fromisoformat(str(expires).replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    except ValueError:
        expired = True

    if not expired:
        return {"status": "UNCHANGED"}

    driver_id = record.get("driver_id")
    await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={
            "id": f"eq.{record.get('id')}",
            "status": "eq.REQUESTED",
            "dispatch_offer_status": "eq.OFFERED",
        },
        json_body={
            "driver_id": None,
            "driver_name": None,
            "driver_phone": None,
            "driver_vehicle": None,
            "driver_plate_number": None,
            "dispatch_offer_status": "NONE",
            "dispatch_offer_expires_at": None,
        },
        action_description="expired ride offer cleanup",
    )

    if driver_id:
        await supabase_request(
            "PATCH",
            SUPABASE_TABLE_DRIVERS,
            params={"driver_id": f"eq.{driver_id}", "availability_status": "eq.BUSY"},
            json_body={"availability_status": "ONLINE"},
            action_description="expired offer driver release",
        )

    refreshed = await get_ride_record(str(record.get("id")))
    return await auto_dispatch_ride(refreshed)


@app.post("/api/driver/auth/login")
async def driver_login(login: DriverLogin):
    stage = "rate_limit"
    try:
        await enforce_shared_rate_limit(
            "driver-login:" + login.driverId,
            AUTH_RATE_LIMIT_MAX,
            AUTH_RATE_LIMIT_WINDOW,
            "Too many driver login attempts. Please try again later.",
        )
        stage = "driver_lookup"
        driver = await get_driver_record(login.driverId)
        stage = "account_status"
        if driver.get("status") != "ACTIVE" or driver.get("verification_status") != "VERIFIED":
            raise HTTPException(status_code=403, detail="Driver account is not active and verified.")
        stage = "pin_verification"
        pin_hash = driver.get("pin_hash")
        if not pin_hash or not verify_password(login.pin, pin_hash):
            raise HTTPException(status_code=401, detail="Invalid driver credentials.")
        stage = "token_creation"
        token = create_driver_token(driver.get("driver_id"))
        return {
            "authenticated": True,
            "driver": {
                "driverId": driver.get("driver_id"),
                "name": driver.get("name"),
                "phone": driver.get("phone"),
                "plateNumber": driver.get("plate_number"),
                "availabilityStatus": driver.get("availability_status", "OFFLINE"),
            },
            "token": token,
        }
    except HTTPException:
        raise
    except Exception as exc:
        import logging
        logging.getLogger("oka_rides.driver_login").exception(
            "Unexpected driver login failure at stage %s", stage
        )
        raise HTTPException(
            status_code=500,
            detail=f"Driver login failed during {stage} ({type(exc).__name__}).",
        ) from exc


@app.get("/api/driver/auth/session")
async def driver_session(driver_id: str = Depends(require_driver_token)):
    driver = await get_driver_record(driver_id)
    return {
        "authenticated": True,
        "driver": {
            "driverId": driver.get("driver_id"),
            "name": driver.get("name"),
            "phone": driver.get("phone"),
            "vehicle": driver.get("vehicle"),
            "plateNumber": driver.get("plate_number"),
            "availabilityStatus": driver.get("availability_status", "OFFLINE"),
        },
    }


@app.patch("/api/driver/availability")
async def driver_availability(
    availability: DriverUpdate,
    driver_id: str = Depends(require_driver_token),
):
    if availability.availabilityStatus not in {"ONLINE", "OFFLINE"}:
        raise HTTPException(status_code=422, detail="Driver availability must be ONLINE or OFFLINE.")
    driver = await get_driver_record(driver_id)
    if driver.get("status") != "ACTIVE" or driver.get("verification_status") != "VERIFIED":
        raise HTTPException(status_code=403, detail="Only ACTIVE and VERIFIED drivers can go ONLINE.")
    if availability.availabilityStatus == "OFFLINE" and driver.get("availability_status") == "BUSY":
        raise HTTPException(status_code=409, detail="Complete or reject the current ride before going OFFLINE.")
    rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_DRIVERS,
        params={"driver_id": f"eq.{driver_id}"},
        json_body={"availability_status": availability.availabilityStatus},
        action_description="driver availability update",
    )
    return {"success": bool(rows), "availabilityStatus": availability.availabilityStatus}


@app.get("/api/driver/rides/current")
async def driver_current_ride(driver_id: str = Depends(require_driver_token)):
    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_RIDES,
        params={
            "select": "*",
            "driver_id": f"eq.{driver_id}",
            "status": "in.(DRIVER ASSIGNED,PAID,PICKED UP)",
            "order": "created_at.desc",
            "limit": "1",
        },
        action_description="driver current ride lookup",
    )
    return {"ride": ride_representation(rows[0]) if rows else None}


@app.patch("/api/driver/rides/{ride_id}/status")
async def driver_update_ride_status(
    status_update: RideStatusUpdate,
    ride_id: str = Path(min_length=1),
    driver_id: str = Depends(require_driver_token),
):
    ride = await get_ride_record(ride_id)
    if ride.get("driver_id") != driver_id:
        raise HTTPException(status_code=403, detail="This ride is not assigned to you.")

    current_status = ride.get("status")
    allowed_transitions = {
        "PAID": {"PICKED UP"},
        "PICKED UP": {"COMPLETED"},
    }
    if status_update.status not in allowed_transitions.get(current_status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition ride from {current_status or 'unknown'} to {status_update.status}.",
        )

    if status_update.status == "COMPLETED":
        driver = await get_driver_record(driver_id)
        old_counter = driver.get("completed_rides", 0)
        if not isinstance(old_counter, int) or isinstance(old_counter, bool) or old_counter < 0:
            raise HTTPException(status_code=502, detail="Driver completed ride count is invalid.")
        await supabase_request(
            "PATCH",
            SUPABASE_TABLE_DRIVERS,
            params={"driver_id": f"eq.{driver_id}", "availability_status": "eq.BUSY"},
            json_body={
                "completed_rides": old_counter + 1,
                "availability_status": "ONLINE",
            },
            action_description="driver completed ride update",
        )

    rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{ride_id}", "driver_id": f"eq.{driver_id}", "status": f"eq.{current_status}"},
        json_body={"status": status_update.status},
        action_description="driver ride status update",
    )
    if not rows:
        raise HTTPException(status_code=409, detail="Ride status changed. Refresh and try again.")

    await write_audit_log(
        "driver", driver_id, "ride.status_updated", "ride", ride_id,
        {"from": current_status, "to": status_update.status},
    )
    return {"success": True, "rideId": ride_id, "status": status_update.status}


@app.get("/api/driver/offers")
async def driver_offers(driver_id: str = Depends(require_driver_token)):
    rows = await supabase_request(
        "GET",
        SUPABASE_TABLE_RIDES,
        params={
            "select": "*",
            "driver_id": f"eq.{driver_id}",
            "dispatch_offer_status": "eq.OFFERED",
            "status": "eq.REQUESTED",
            "order": "dispatched_at.desc",
            "limit": "10",
        },
        action_description="driver offer lookup",
    )
    return {"offers": [ride_representation(row) for row in rows]}


@app.post("/api/driver/rides/{ride_id}/accept")
async def driver_accept_ride(
    ride_id: str = Path(min_length=1),
    driver_id: str = Depends(require_driver_token),
):
    ride = await get_ride_record(ride_id)
    if ride.get("driver_id") != driver_id or ride.get("dispatch_offer_status") != "OFFERED":
        raise HTTPException(status_code=409, detail="This ride is not offered to this driver.")
    expires = ride.get("dispatch_offer_expires_at")
    if expires:
        try:
            if datetime.fromisoformat(str(expires).replace("Z", "+00:00")) < datetime.now(timezone.utc):
                raise HTTPException(status_code=409, detail="This ride offer has expired.")
        except ValueError:
            raise HTTPException(status_code=502, detail="Ride offer expiry is invalid.")

    rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={
            "id": f"eq.{ride_id}",
            "driver_id": f"eq.{driver_id}",
            "status": "eq.REQUESTED",
            "dispatch_offer_status": "eq.OFFERED",
        },
        json_body={"status": "DRIVER ASSIGNED", "dispatch_offer_status": "ACCEPTED"},
        action_description="driver ride acceptance",
    )
    if not rows:
        raise HTTPException(status_code=409, detail="Ride is no longer available.")
    await write_audit_log("driver", driver_id, "ride.driver_accepted", "ride", ride_id, {})
    return {"success": True, "rideId": ride_id, "status": "DRIVER ASSIGNED"}


@app.post("/api/driver/rides/{ride_id}/reject")
async def driver_reject_ride(
    ride_id: str = Path(min_length=1),
    driver_id: str = Depends(require_driver_token),
):
    ride = await get_ride_record(ride_id)
    if ride.get("driver_id") != driver_id or ride.get("dispatch_offer_status") != "OFFERED":
        raise HTTPException(status_code=409, detail="This ride is not offered to this driver.")

    rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={
            "id": f"eq.{ride_id}",
            "driver_id": f"eq.{driver_id}",
            "status": "eq.REQUESTED",
            "dispatch_offer_status": "eq.OFFERED",
        },
        json_body={
            "driver_id": None,
            "driver_name": None,
            "driver_phone": None,
            "driver_vehicle": None,
            "driver_plate_number": None,
            "dispatch_offer_status": "NONE",
            "dispatch_offer_expires_at": None,
        },
        action_description="driver ride rejection",
    )
    if not rows:
        raise HTTPException(status_code=409, detail="Ride is no longer available.")
    await supabase_request(
        "PATCH",
        SUPABASE_TABLE_DRIVERS,
        params={"driver_id": f"eq.{driver_id}", "availability_status": "eq.BUSY"},
        json_body={"availability_status": "ONLINE"},
        action_description="driver rejection availability reset",
    )
    await write_audit_log("driver", driver_id, "ride.driver_rejected", "ride", ride_id, {})
    refreshed = await get_ride_record(ride_id)
    dispatch = await auto_dispatch_ride(refreshed)
    return {"success": True, "rideId": ride_id, "status": "REQUESTED", "dispatch": dispatch}


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
        params={"select": "*", "limit": "1000"},
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
        "dispatchMode": "AUTOMATIC_WITH_MANUAL_OVERRIDE",
        "message": "The system offers rides automatically; dispatcher can override when needed.",
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
        raise HTTPException(status_code=409, detail="Driver must be ACTIVE, VERIFIED, and ONLINE before dispatch.")

    ride = await get_ride_record(ride_id)
    if ride.get("status") != "REQUESTED":
        raise HTTPException(status_code=409, detail="Only REQUESTED rides can be assigned.")

    if ride.get("dispatch_offer_status") == "OFFERED" and ride.get("driver_id"):
        await supabase_request(
            "PATCH",
            SUPABASE_TABLE_DRIVERS,
            params={"driver_id": f"eq.{ride.get('driver_id')}", "availability_status": "eq.BUSY"},
            json_body={"availability_status": "ONLINE"},
            action_description="dispatcher offer override release",
        )

    fields = {
        "status": "DRIVER ASSIGNED",
        "driver_id": driver.get("driver_id"),
        "driver_name": driver.get("name"),
        "driver_phone": driver.get("phone"),
        "driver_vehicle": driver.get("vehicle"),
        "driver_plate_number": driver.get("plate_number"),
    }

    driver_rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_DRIVERS,
        params={"driver_id": f"eq.{driver_id}", "availability_status": "eq.ONLINE"},
        json_body={"availability_status": "BUSY"},
        action_description="driver availability update",
    )
    if not driver_rows:
        raise HTTPException(status_code=409, detail="Driver is no longer available for dispatch.")

    ride_rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{ride_id}", "status": "eq.REQUESTED"},
        json_body=fields,
        action_description="ride assignment",
    )
    if not ride_rows:
        await supabase_request(
            "PATCH",
            SUPABASE_TABLE_DRIVERS,
            params={"driver_id": f"eq.{driver_id}", "availability_status": "eq.BUSY"},
            json_body={"availability_status": "ONLINE"},
            action_description="driver reservation rollback",
        )
        raise HTTPException(status_code=409, detail="Ride was already dispatched or changed.")

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
    await write_audit_log("dispatcher", _, "ride.driver_assigned", "ride", ride_id, {"driver_id": driver.get("driver_id")})

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
        raise HTTPException(status_code=409, detail="Fare can only be confirmed for DRIVER ASSIGNED rides.")
    if not fare_update.fareConfirmed:
        raise HTTPException(status_code=422, detail="Fare must be confirmed before payment can proceed.")

    rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{ride_id}", "status": "eq.DRIVER ASSIGNED"},
        json_body={"fare": fare_update.fare, "fare_confirmed": True, "payment_status": "PENDING"},
        action_description="fare confirmation",
    )
    if not rows:
        raise HTTPException(status_code=409, detail="Ride state changed. Refresh and try again.")

    await write_audit_log("dispatcher", _, "ride.fare_confirmed", "ride", ride_id, {"fare": fare_update.fare})
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
        raise HTTPException(status_code=409, detail="Payment can only be verified for DRIVER ASSIGNED rides.")
    if not ride.get("fare") or ride.get("fare_confirmed") is not True:
        raise HTTPException(status_code=409, detail="Payment requires a confirmed fare.")
    if payment.paymentStatus == "PAID" and not payment.paymentReference.strip():
        raise HTTPException(status_code=422, detail="Payment reference is required for PAID payments.")
    if payment.paymentStatus == "PAID" and ride.get("payment_method") == "PAYSTACK":
        raise HTTPException(status_code=409, detail="Paystack payments must be confirmed by Paystack verification, not recorded manually.")

    fields = {"payment_status": payment.paymentStatus}
    if payment.paymentStatus == "PAID":
        # Persist the ledger first; retrying is safe by payment reference.
        await record_payment_ledger(
            ride, payment.paymentReference.strip(), naira_to_kobo(ride.get("fare")),
            "OFFLINE", "PAID", {"recorded_by": "dispatcher"},
        )
        fields.update({
            "payment_reference": payment.paymentReference.strip(),
            "payment_method": "OPAY",
            "status": "PAID",
        })
    else:
        fields.update({"payment_reference": None, "payment_method": None})

    payment_rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{ride_id}", "status": "eq.DRIVER ASSIGNED"},
        json_body=fields,
        action_description="payment update",
    )
    if not payment_rows:
        raise HTTPException(status_code=409, detail="Ride payment state changed. Refresh and try again.")

    await write_audit_log(
        "dispatcher", _, "ride.payment_updated", "ride", ride_id,
        {"payment_status": payment.paymentStatus, "method": "OFFLINE"},
    )
    return {
        "success": True,
        "rideId": ride_id,
        "paymentStatus": payment.paymentStatus,
        "rideStatus": "PAID" if payment.paymentStatus == "PAID" else "DRIVER ASSIGNED",
    }


def paystack_secret_key() -> str:
    key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="Paystack is not configured on the server.")
    return key


PAYSTACK_PLATFORM_COMMISSION_PERCENT = 15


def paystack_split_config(driver: dict) -> dict:
    """Route 85% to the assigned driver and retain 15% on the platform account."""
    subaccount_code = str(driver.get("paystack_subaccount_code") or "").strip()
    if not subaccount_code.startswith("ACCT_"):
        raise HTTPException(
            status_code=409,
            detail="The assigned driver does not have a valid Paystack subaccount configured.",
        )
    return {
        "split": {
            "type": "percentage",
            "bearer_type": "account",
            "subaccounts": [{"subaccount": subaccount_code, "share": 85}],
        },
    }


def naira_to_kobo(amount) -> int:
    try:
        value = float(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError("Amount must be a positive number.") from exc
    if value <= 0:
        raise ValueError("Amount must be positive.")
    return int(round(value * 100))


def paystack_amount_matches(amount_kobo, fare) -> bool:
    try:
        return int(amount_kobo) == naira_to_kobo(fare)
    except (TypeError, ValueError):
        return False


def paystack_payment_already_applied(ride: dict, reference: str) -> bool:
    status = ride.get("payment_status", ride.get("Payment Status"))
    ride_status = ride.get("status", ride.get("Status"))
    applied_reference = ride.get("payment_reference", ride.get("Payment Reference"))
    return status == "PAID" and ride_status == "PAID" and applied_reference == reference


def paystack_signature_is_valid(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(signature, expected)


async def paystack_api(method: str, path: str, payload: dict | None = None) -> dict:
    secret = paystack_secret_key()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                method,
                f"https://api.paystack.co{path}",
                headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to connect to Paystack.") from exc
    try:
        result = response.json()
    except ValueError:
        result = {}
    if response.is_error or result.get("status") is not True:
        raise HTTPException(status_code=502, detail="Paystack could not process the payment request.")
    return result.get("data") or {}


def paystack_expected_amount_kobo(ride: dict) -> int:
    try:
        return naira_to_kobo(ride.get("fare"))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="A valid confirmed fare is required.") from exc


def validate_paystack_transaction(transaction: dict, ride: dict, reference: str) -> None:
    if transaction.get("status") != "success":
        raise HTTPException(status_code=409, detail="Paystack has not confirmed a successful payment.")
    if str(transaction.get("reference") or "") != reference:
        raise HTTPException(status_code=409, detail="Paystack transaction reference does not match.")
    if transaction.get("currency") != "NGN":
        raise HTTPException(status_code=409, detail="Paystack transaction currency does not match NGN.")
    if not paystack_amount_matches(transaction.get("amount"), ride.get("fare")):
        raise HTTPException(status_code=409, detail="Paystack transaction amount does not match the ride fare.")
    metadata = transaction.get("metadata") or {}
    if str(metadata.get("ride_id") or "") != str(ride.get("id") or ""):
        raise HTTPException(status_code=409, detail="Paystack transaction is not linked to this ride.")
    if str(ride.get("payment_reference") or "") != reference:
        raise HTTPException(status_code=409, detail="Payment reference is no longer active for this ride.")


async def record_payment_ledger(ride: dict, reference: str, amount_kobo: int, method: str, status: str, provider_data: dict | None = None) -> dict:
    """Persist a payment record keyed by its unique provider/manual reference."""
    existing = await supabase_request(
        "GET", "oka_rides_payment_records",
        params={"select": "*", "reference": f"eq.{reference}", "limit": "1"},
        action_description="payment record lookup",
    )
    payload = {
        "ride_id": str(ride.get("id")),
        "ride_reference": str(ride.get("reference") or ""),
        "reference": reference,
        "amount_kobo": int(amount_kobo),
        "currency": "NGN",
        "method": method,
        "status": status,
        "provider": "paystack" if method == "PAYSTACK" else "offline",
        "provider_data": provider_data or {},
        "paid_at": datetime.now(timezone.utc).isoformat() if status == "PAID" else None,
    }
    if existing:
        record = existing[0]
        if str(record.get("ride_id")) != str(ride.get("id")) or int(record.get("amount_kobo") or 0) != int(amount_kobo):
            raise HTTPException(status_code=409, detail="Payment reference is already linked to a different payment.")
        if record.get("status") == "PAID" and status != "PAID":
            return record
        rows = await supabase_request("PATCH", "oka_rides_payment_records",
            params={"id": f"eq.{record.get('id')}"}, json_body=payload,
            action_description="payment record update")
        return rows[0] if rows else record
    try:
        rows = await supabase_request("POST", "oka_rides_payment_records", json_body=payload,
            action_description="payment record insert")
        return rows[0] if rows else payload
    except HTTPException:
        # If simultaneous webhook/callback requests race on the unique reference,
        # accept the record created by the other request only if it matches.
        existing = await supabase_request("GET", "oka_rides_payment_records",
            params={"select": "*", "reference": f"eq.{reference}", "limit": "1"},
            action_description="payment record retry lookup")
        if existing and str(existing[0].get("ride_id")) == str(ride.get("id")) and int(existing[0].get("amount_kobo") or 0) == int(amount_kobo):
            return existing[0]
        raise


async def finalize_paystack_payment(ride: dict, reference: str, transaction: dict) -> dict:
    validate_paystack_transaction(transaction, ride, reference)
    amount_kobo = int(transaction.get("amount"))
    if paystack_payment_already_applied(ride, reference):
        await record_payment_ledger(ride, reference, amount_kobo, "PAYSTACK", "PAID", {
            "transaction_id": transaction.get("id"), "channel": transaction.get("channel"),
        })
        return {"success": True, "paymentStatus": "PAID", "rideStatus": "PAID"}
    if ride.get("status") != "DRIVER ASSIGNED" or ride.get("fare_confirmed") is not True:
        raise HTTPException(status_code=409, detail="Payment can only be applied to a ride with a confirmed fare and assigned driver.")
    await record_payment_ledger(ride, reference, amount_kobo, "PAYSTACK", "PAID", {
        "transaction_id": transaction.get("id"), "channel": transaction.get("channel"),
    })
    rows = await supabase_request(
        "PATCH", SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{ride.get('id')}", "status": "eq.DRIVER ASSIGNED",
                "payment_status": "neq.PAID", "payment_reference": f"eq.{reference}"},
        json_body={"payment_status": "PAID", "payment_reference": reference,
                   "payment_method": "PAYSTACK", "status": "PAID"},
        action_description="Paystack payment confirmation",
    )
    if not rows:
        current = await get_ride_record(str(ride.get("id")))
        if current.get("payment_status") == "PAID" and current.get("status") == "PAID" and current.get("payment_reference") == reference:
            return {"success": True, "paymentStatus": "PAID", "rideStatus": "PAID"}
        raise HTTPException(status_code=409, detail="Payment record saved but ride state changed. Dispatcher reconciliation is required.")
    await write_audit_log("paystack", "paystack", "ride.paystack_payment_confirmed", "ride",
                          str(ride.get("id")), {"reference": reference, "amount": transaction.get("amount")})
    return {"success": True, "paymentStatus": "PAID", "rideStatus": "PAID"}

@app.post("/api/customer/rides/{reference}/paystack/initialize")
async def initialize_paystack_payment(
    request: Request,
    reference: str = Path(min_length=1),
    customer_id: str = Depends(require_customer_session),
):
    ride = await get_customer_ride(reference, customer_id)
    if ride.get("status") != "DRIVER ASSIGNED" or ride.get("fare_confirmed") is not True:
        raise HTTPException(status_code=409, detail="Payment is available after a driver is assigned and the fare is confirmed.")
    if ride.get("payment_status") == "PAID":
        raise HTTPException(status_code=409, detail="This ride is already paid.")
    amount = paystack_expected_amount_kobo(ride)
    email = str(ride.get("email") or "").strip()
    if not email:
        customer = await get_customer_record(customer_id)
        email = str(customer.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=422, detail="A valid customer email is required for Paystack checkout.")

    driver_id = str(ride.get("driver_id") or "").strip()
    if not driver_id:
        raise HTTPException(status_code=409, detail="This ride has no assigned driver for payout.")
    driver = await get_driver_record(driver_id)
    if driver.get("status") != "ACTIVE" or driver.get("verification_status") != "VERIFIED":
        raise HTTPException(status_code=409, detail="The assigned driver is not eligible to receive payments.")
    split_config = paystack_split_config(driver)

    payment_reference = f"OKA-{secrets.token_hex(12).upper()}"
    rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{ride.get('id')}", "customer_id": f"eq.{customer_id}", "status": "eq.DRIVER ASSIGNED", "payment_status": "neq.PAID"},
        json_body={"payment_reference": payment_reference, "payment_method": "PAYSTACK", "payment_status": "PENDING"},
        action_description="Paystack payment initialization",
    )
    if not rows:
        raise HTTPException(status_code=409, detail="Ride payment state changed. Refresh and try again.")

    request_origin = request.headers.get("origin", "").rstrip("/")
    callback_base = os.getenv("PAYSTACK_CALLBACK_URL", "").strip().rstrip("/")
    if not callback_base:
        callback_base = request_origin if request_origin in frontend_origins else frontend_origins[0]
    callback_url = f"{callback_base}/?paystack=callback&ride={reference}"
    try:
        checkout = await paystack_api("POST", "/transaction/initialize", {
            "email": email,
            "amount": amount,
            "currency": "NGN",
            "reference": payment_reference,
            "callback_url": callback_url,
            "metadata": {"ride_id": str(ride.get("id")), "ride_reference": reference},
            **split_config,
        })
    except HTTPException:
        await supabase_request(
            "PATCH", SUPABASE_TABLE_RIDES,
            params={"id": f"eq.{ride.get('id')}", "payment_reference": f"eq.{payment_reference}"},
            json_body={"payment_reference": None, "payment_method": None},
            action_description="Paystack initialization rollback",
        )
        raise
    authorization_url = checkout.get("authorization_url")
    if not authorization_url or not str(authorization_url).startswith("https://checkout.paystack.com/"):
        raise HTTPException(status_code=502, detail="Paystack did not return a valid checkout URL.")
    return {"authorizationUrl": authorization_url, "reference": payment_reference, "amount": amount, "currency": "NGN"}


@app.post("/api/customer/rides/{reference}/paystack/verify")
async def verify_customer_paystack_payment(
    body: PaystackVerifyRequest,
    reference: str = Path(min_length=1),
    customer_id: str = Depends(require_customer_session),
):
    ride = await get_customer_ride(reference, customer_id)
    if ride.get("payment_status") == "PAID" and ride.get("status") == "PAID" and ride.get("payment_reference") == body.reference:
        return {"success": True, "paymentStatus": "PAID", "rideStatus": "PAID"}
    if ride.get("status") != "DRIVER ASSIGNED" or ride.get("fare_confirmed") is not True:
        raise HTTPException(status_code=409, detail="Ride is not eligible for payment verification.")
    if str(ride.get("payment_reference") or "") != body.reference:
        raise HTTPException(status_code=409, detail="Payment reference does not match this ride.")
    transaction = await paystack_api("GET", f"/transaction/verify/{body.reference}")
    return await finalize_paystack_payment(ride, body.reference, transaction)


@app.post("/api/payments/paystack/webhook")
async def paystack_webhook(request: Request):
    secret = paystack_secret_key()
    raw_body = await request.body()
    supplied_signature = request.headers.get("x-paystack-signature", "")
    if not paystack_signature_is_valid(raw_body, supplied_signature, secret):
        raise HTTPException(status_code=401, detail="Invalid Paystack webhook signature.")
    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid webhook payload.")
    if event.get("event") != "charge.success":
        return {"received": True}
    data = event.get("data") or {}
    reference = str(data.get("reference") or "")
    metadata = data.get("metadata") or {}
    ride_id = str(metadata.get("ride_id") or "")
    if not reference or not ride_id:
        return {"received": True}
    try:
        ride = await get_ride_record(ride_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return {"received": True}
        raise
    if str(ride.get("payment_reference") or "") != reference:
        return {"received": True}
    transaction = await paystack_api("GET", f"/transaction/verify/{reference}")
    await finalize_paystack_payment(ride, reference, transaction)
    return {"received": True}


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
            detail=f"Cannot transition ride from {current_status or 'unknown'} to {status_update.status}.",
        )

    assigned_driver_id = ride.get("driver_id")
    changes_driver_counter = status_update.status in {"COMPLETED", "CANCELLED"}
    is_requested_cancellation = status_update.status == "CANCELLED" and current_status == "REQUESTED"

    if changes_driver_counter and not is_requested_cancellation:
        if not assigned_driver_id:
            raise HTTPException(status_code=409, detail="This transition requires an assigned driver.")

        driver = await get_driver_record(assigned_driver_id)
        counter_field = "completed_rides" if status_update.status == "COMPLETED" else "cancelled_rides"
        old_counter = driver.get(counter_field, 0)
        if not isinstance(old_counter, int) or isinstance(old_counter, bool) or old_counter < 0:
            raise HTTPException(status_code=502, detail=f"Assigned driver's {counter_field} value is invalid.")

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

    status_rows = await supabase_request(
        "PATCH",
        SUPABASE_TABLE_RIDES,
        params={"id": f"eq.{ride_id}", "status": f"eq.{current_status}"},
        json_body={"status": status_update.status},
        action_description="ride status update",
    )
    if not status_rows:
        raise HTTPException(status_code=409, detail="Ride status changed. Refresh and try again.")

    await write_audit_log(
        "dispatcher", _, "ride.status_updated", "ride", ride_id,
        {"from": current_status, "to": status_update.status},
    )
    return {
        "success": True,
        "rideId": ride_id,
        "previousStatus": current_status,
        "status": status_update.status,
    }
