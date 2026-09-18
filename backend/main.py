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
from pydantic import BaseModel, Field

load_dotenv()

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
    email: str = Field(min_length=3)
    pickup: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    pickupTiming: str = Field(min_length=1)
    scheduledTime: str = ""
    passengers: str = Field(min_length=1)
    notes: str = ""
    vehicle: str = Field(min_length=1)
    reference: str = Field(min_length=1)
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
    notes: str | None = None


class FareUpdate(BaseModel):
    fare: float = Field(gt=0)
    fareConfirmed: bool


class PaymentUpdate(BaseModel):
    paymentStatus: Literal["PAID", "FAILED"]
    paymentReference: str = ""
    paymentMethod: Literal["OPAY", "CASH", "PAYSTACK"]


class RideStatusUpdate(BaseModel):
    status: Literal["PICKED UP", "COMPLETED", "CANCELLED"]


class DispatcherLogin(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


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
PASSWORD_HASH_PREFIX = "pbkdf2_sha256"


def airtable_settings():
    api_key = os.getenv("AIRTABLE_API_KEY")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    table_name = os.getenv("AIRTABLE_TABLE_NAME")
    if not api_key or not base_id or not table_name:
        raise HTTPException(
            status_code=500,
            detail="Airtable configuration is incomplete on the server.",
        )
    return api_key, base_id, table_name


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
        "sha256", password.encode(), salt, iterations
    )
    return hmac.compare_digest(actual_digest, expected_digest)


def sign_session(payload: str, secret: str):
    signature = hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{payload}.{encoded_signature}"


def create_session(username: str, secret: str):
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"sub": username, "exp": int(time.time()) + DISPATCHER_SESSION_TTL},
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
        default=None, alias=DISPATCHER_SESSION_COOKIE
    ),
):
    username, _, session_secret = dispatcher_settings()
    if session_username(session_cookie, session_secret) != username:
        raise HTTPException(
            status_code=401,
            detail="Dispatcher authentication required.",
        )
    return username


def airtable_headers(api_key: str):
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def airtable_request(method: str, url: str, api_key: str, **kwargs):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.request(
                method,
                url,
                headers=airtable_headers(api_key),
                **kwargs,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to connect to Airtable.",
        ) from exc
    return response


def ride_representation(record: dict):
    fields = record.get("fields", {})
    return {
        "id": record.get("id"),
        "reference": fields.get("Reference"),
        "name": fields.get("Name"),
        "phone": fields.get("Phone"),
        "pickup": fields.get("Pickup"),
        "destination": fields.get("Destination"),
        "pickupTiming": fields.get("Pickup Timing"),
        "scheduledTime": fields.get("Scheduled Time"),
        "passengers": fields.get("Passengers"),
        "notes": fields.get("Notes"),
        "vehicle": fields.get("Vehicle"),
        "status": fields.get("Status"),
        "fare": fields.get("Fare"),
        "fareConfirmed": fields.get("Fare Confirmed", False),
        "paymentStatus": fields.get("Payment Status"),
        "paymentReference": fields.get("Payment Reference"),
        "paymentMethod": fields.get("Payment Method"),
        "pickupLatitude": fields.get("Pickup Latitude"),
        "pickupLongitude": fields.get("Pickup Longitude"),
        "destinationLatitude": fields.get("Destination Latitude"),
        "destinationLongitude": fields.get("Destination Longitude"),
        "driverId": fields.get("Driver ID"),
        "driverName": fields.get("Driver Name"),
        "driverPhone": fields.get("Driver Phone"),
        "driverVehicle": fields.get("Driver Vehicle"),
        "driverPlateNumber": fields.get("Driver Plate Number"),
        "createdAt": fields.get("Created At"),
    }


def airtable_read_error(response, resource: str):
    if response.status_code in {401, 403}:
        return HTTPException(
            status_code=502,
            detail="Airtable authentication failed.",
        )
    return HTTPException(
        status_code=502,
        detail=f"Airtable rejected the {resource} request.",
    )


def created_at_sort_key(ride: dict):
    created_at = ride.get("createdAt")
    if not created_at:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


async def get_assigned_driver(
    base_id: str,
    driver_id: str,
    api_key: str,
):
    escaped_driver_id = driver_id.replace("'", "''")
    response = await airtable_request(
        "GET",
        f"https://api.airtable.com/v0/{base_id}/Drivers",
        api_key,
        params={"filterByFormula": f"{{Driver ID}}='{escaped_driver_id}'", "pageSize": 2},
    )
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Airtable rejected the assigned driver lookup.",
        )

    records = response.json().get("records", [])
    if not records:
        raise HTTPException(status_code=409, detail="Assigned driver was not found.")
    return records[0]


PAYSTACK_API_BASE = "https://api.paystack.co"

def paystack_secret():
    secret = os.getenv("PAYSTACK_SECRET_KEY")
    if not secret:
        raise HTTPException(status_code=500, detail="Paystack is not configured on the server.")
    return secret

def paystack_callback_url():
    callback = os.getenv("PAYSTACK_CALLBACK_URL")
    if not callback:
        raise HTTPException(status_code=500, detail="Paystack callback URL is not configured.")
    return callback

def naira_to_kobo(amount: float) -> int:
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    kobo = round(amount * 100)
    if kobo <= 0:
        raise ValueError("Amount must be greater than zero.")
    return kobo

def paystack_signature_is_valid(raw_body: bytes, supplied_signature: str | None, secret: str) -> bool:
    if not supplied_signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, supplied_signature)

async def find_ride_by_reference(reference: str, api_key: str, base_id: str, table_name: str):
    escaped = reference.replace("'", "''")
    response = await airtable_request(
        "GET",
        f"https://api.airtable.com/v0/{base_id}/{table_name}",
        api_key,
        params={"filterByFormula": f"{{Reference}}='{escaped}'", "pageSize": 2},
    )
    if response.is_error:
        raise HTTPException(status_code=502, detail="Airtable rejected the ride lookup.")
    records = response.json().get("records", [])
    if not records:
        raise HTTPException(status_code=404, detail="Ride not found.")
    return records[0]

async def verify_paystack_transaction(reference: str, expected_amount_kobo: int, secret: str):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{PAYSTACK_API_BASE}/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {secret}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to verify payment with Paystack.") from exc
    if response.is_error:
        raise HTTPException(status_code=502, detail="Paystack rejected the payment verification request.")
    payload = response.json()
    data = payload.get("data") or {}
    if payload.get("status") is not True or data.get("status") != "success":
        raise HTTPException(status_code=402, detail="Paystack payment was not successful.")
    if data.get("reference") != reference:
        raise HTTPException(status_code=409, detail="Paystack reference mismatch.")
    if data.get("currency") != "NGN" or data.get("amount") != expected_amount_kobo:
        raise HTTPException(status_code=409, detail="Paystack payment amount mismatch.")
    return data

async def mark_paystack_paid(record: dict, paystack_data: dict, api_key: str, base_id: str, table_name: str):
    fields = record.get("fields", {})
    if fields.get("Status") == "PAID" and fields.get("Payment Reference") == paystack_data.get("reference"):
        return False
    if fields.get("Payment Status") == "PAID":
        if fields.get("Payment Reference") == paystack_data.get("reference"):
            return False
        raise HTTPException(status_code=409, detail="Ride already has a different successful payment.")
    fare = fields.get("Fare")
    if not fields.get("Fare Confirmed") or not fare:
        raise HTTPException(status_code=409, detail="Ride does not have a confirmed fare.")
    expected = naira_to_kobo(float(fare))
    if paystack_data.get("amount") != expected:
        raise HTTPException(status_code=409, detail="Paystack payment amount mismatch.")
    ride_url = f"https://api.airtable.com/v0/{base_id}/{table_name}/{record['id']}"
    response = await airtable_request(
        "PATCH", ride_url, api_key,
        json={"fields": {
            "Payment Status": "PAID",
            "Payment Reference": paystack_data["reference"],
            "Payment Method": "PAYSTACK",
            "Status": "PAID",
        }},
    )
    if response.is_error:
        raise HTTPException(status_code=502, detail="Airtable rejected the payment update.")
    return True

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "Awka Rides API is running"}


@app.post("/api/auth/login")
async def dispatcher_login(credentials: DispatcherLogin, response: Response):
    username, password_hash, session_secret = dispatcher_settings()
    if (
        not hmac.compare_digest(credentials.username, username)
        or not verify_password(credentials.password, password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid dispatcher credentials.")

    secure_cookie = os.getenv("DISPATCHER_COOKIE_SECURE", "true").lower() == "true"
    response.set_cookie(
        key=DISPATCHER_SESSION_COOKIE,
        value=create_session(username, session_secret),
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
async def create_ride(ride: RideRequest):
    api_key, base_id, table_name = airtable_settings()
    try:
        passengers = int(ride.passengers)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Passengers must be a whole number.",
        ) from exc

    airtable_url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
    fields = {
        "Reference": ride.reference,
        "Name": ride.name,
        "Phone": ride.phone,
        "Pickup": ride.pickup,
        "Destination": ride.destination,
        "Pickup Timing": ride.pickupTiming,
        "Passengers": passengers,
        "Notes": ride.notes,
        "Vehicle": ride.vehicle,
        "Status": "REQUESTED",
        "Fare Confirmed": False,
        "Payment Status": "PENDING",
        "Created At": datetime.now(timezone.utc).isoformat(),
    }
    if ride.scheduledTime:
        fields["Scheduled Time"] = ride.scheduledTime
    for field_name, value in {
        "Pickup Latitude": ride.pickupLatitude,
        "Pickup Longitude": ride.pickupLongitude,
        "Destination Latitude": ride.destinationLatitude,
        "Destination Longitude": ride.destinationLongitude,
    }.items():
        if value is not None:
            fields[field_name] = value

    response = await airtable_request(
        "POST", airtable_url, api_key, json={"fields": fields}
    )
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Airtable rejected the ride request.",
        )

    return {
        "message": "Ride request created",
        "reference": ride.reference,
        "status": "REQUESTED",
        "airtableRecordId": response.json().get("id"),
    }


@app.get("/api/rides/public/{reference}")
async def public_ride_status(reference: str = Path(min_length=1)):
    api_key, base_id, table_name = airtable_settings()
    record = await find_ride_by_reference(reference, api_key, base_id, table_name)
    fields = record.get("fields", {})
    return {
        "reference": fields.get("Reference"),
        "status": fields.get("Status"),
        "fare": fields.get("Fare"),
        "fareConfirmed": fields.get("Fare Confirmed", False),
        "paymentStatus": fields.get("Payment Status"),
    }


class PaystackInitialize(BaseModel):
    reference: str = Field(min_length=1)
    email: str = Field(min_length=3)

@app.post("/api/payments/paystack/initialize")
async def initialize_paystack_payment(payment: PaystackInitialize):
    api_key, base_id, table_name = airtable_settings()
    secret = paystack_secret()
    record = await find_ride_by_reference(payment.reference, api_key, base_id, table_name)
    fields = record.get("fields", {})
    if fields.get("Status") != "DRIVER ASSIGNED" or fields.get("Fare Confirmed") is not True:
        raise HTTPException(status_code=409, detail="Payment is only available after a driver is assigned and the fare is confirmed.")
    if fields.get("Payment Status") == "PAID":
        raise HTTPException(status_code=409, detail="This ride has already been paid.")
    fare = fields.get("Fare")
    try:
        amount_kobo = naira_to_kobo(float(fare))
    except (TypeError, ValueError):
        raise HTTPException(status_code=409, detail="This ride does not have a valid confirmed fare.")
    safe_reference = "OKARIDE-" + "".join(ch for ch in payment.reference if ch.isalnum() or ch in "-.=") + "-" + secrets.token_hex(4)
    payload = {
        "email": payment.email.strip(),
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
                headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to initialize payment with Paystack.") from exc
    if response.is_error or response.json().get("status") is not True:
        raise HTTPException(status_code=502, detail="Paystack could not initialize the payment.")
    data = response.json().get("data") or {}
    return {"authorizationUrl": data.get("authorization_url"), "accessCode": data.get("access_code"), "reference": data.get("reference")}

@app.get("/api/payments/paystack/callback")
async def paystack_callback(reference: str = Query(min_length=1)):
    api_key, base_id, table_name = airtable_settings()
    secret = paystack_secret()
    # Find the ride by the metadata reference by verifying first, then use metadata.
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{PAYSTACK_API_BASE}/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {secret}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Unable to verify payment with Paystack.") from exc
    if response.is_error:
        raise HTTPException(status_code=502, detail="Paystack verification failed.")
    data = response.json().get("data") or {}
    ride_reference = (data.get("metadata") or {}).get("ride_reference")
    if not ride_reference:
        raise HTTPException(status_code=400, detail="Payment is missing ride metadata.")
    record = await find_ride_by_reference(ride_reference, api_key, base_id, table_name)
    fare = record.get("fields", {}).get("Fare")
    if not fare:
        raise HTTPException(status_code=409, detail="Ride fare is unavailable.")
    verified = await verify_paystack_transaction(reference, naira_to_kobo(float(fare)), secret)
    await mark_paystack_paid(record, verified, api_key, base_id, table_name)
    frontend = os.getenv("FRONTEND_ORIGIN", "").split(",")[0].strip()
    if frontend:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"{frontend.rstrip('/')}/success.html?payment=success&reference={reference}")
    return {"success": True, "reference": reference}

@app.post("/api/payments/paystack/webhook")
async def paystack_webhook(request: Request):
    secret = paystack_secret()
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature")
    if not paystack_signature_is_valid(raw_body, signature, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")
    event = json.loads(raw_body.decode("utf-8"))
    if event.get("event") != "charge.success":
        return {"received": True}
    data = event.get("data") or {}
    reference = data.get("reference")
    if not reference:
        return {"received": True}
    api_key, base_id, table_name = airtable_settings()
    ride_reference = (data.get("metadata") or {}).get("ride_reference")
    if not ride_reference:
        return {"received": True}
    record = await find_ride_by_reference(ride_reference, api_key, base_id, table_name)
    fare = record.get("fields", {}).get("Fare")
    if not fare:
        raise HTTPException(status_code=409, detail="Ride fare is unavailable.")
    verified = await verify_paystack_transaction(reference, naira_to_kobo(float(fare)), secret)
    await mark_paystack_paid(record, verified, api_key, base_id, table_name)
    return {"received": True, "processed": True}

@app.get("/api/rides")
async def list_rides(
    status: RideListStatus | None = Query(default=None),
    _: str = Depends(require_dispatcher_session),
):
    api_key, base_id, table_name = airtable_settings()
    url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
    params = {"pageSize": 100}
    if status is not None:
        escaped_status = status.replace("'", "''")
        params["filterByFormula"] = f"{{Status}}='{escaped_status}'"

    response = await airtable_request("GET", url, api_key, params=params)
    if response.is_error:
        raise airtable_read_error(response, "ride list")

    rides = [
        ride_representation(record)
        for record in response.json().get("records", [])
    ]
    rides.sort(key=created_at_sort_key, reverse=True)
    return {"rides": rides}


@app.get("/api/rides/{ride_id}")
async def get_ride(
    ride_id: str = Path(min_length=1),
    _: str = Depends(require_dispatcher_session),
):
    api_key, base_id, table_name = airtable_settings()
    url = f"https://api.airtable.com/v0/{base_id}/{table_name}/{ride_id}"
    response = await airtable_request("GET", url, api_key)
    if response.status_code in {404, 422}:
        raise HTTPException(status_code=404, detail="Ride not found.")
    if response.status_code == 403:
        escaped_ride_id = ride_id.replace("'", "''")
        fallback_response = await airtable_request(
            "GET",
            f"https://api.airtable.com/v0/{base_id}/{table_name}",
            api_key,
            params={
                "filterByFormula": f"RECORD_ID()='{escaped_ride_id}'",
                "pageSize": 1,
            },
        )
        if fallback_response.is_success:
            records = fallback_response.json().get("records", [])
            if not records:
                raise HTTPException(status_code=404, detail="Ride not found.")
            return ride_representation(records[0])
    if response.is_error:
        raise airtable_read_error(response, "ride lookup")
    return ride_representation(response.json())


@app.get("/api/drivers")
async def list_drivers(_: str = Depends(require_dispatcher_session)):
    api_key, base_id, _ = airtable_settings()
    url = f"https://api.airtable.com/v0/{base_id}/Drivers"
    response = await airtable_request(
        "GET",
        url,
        api_key,
        params={"pageSize": 100},
    )
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Airtable rejected the driver list request.",
        )

    drivers = []
    for record in response.json().get("records", []):
        fields = record.get("fields", {})
        status = fields.get("Status", "INACTIVE")
        verification_status = fields.get("Verification Status", "PENDING")
        drivers.append(
            {
                "id": record["id"],
                "driverId": fields.get("Driver ID"),
                "name": fields.get("Name"),
                "phone": fields.get("Phone"),
                "vehicle": fields.get("Vehicle"),
                "plateNumber": fields.get("Plate Number"),
                "status": status,
                "verificationStatus": verification_status,
                "completedRides": fields.get("Completed Rides", 0),
                "cancelledRides": fields.get("Cancelled Rides", 0),
                "notes": fields.get("Notes"),
                "eligible": status == "ACTIVE" and verification_status == "VERIFIED",
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
    api_key, base_id, _ = airtable_settings()
    url = f"https://api.airtable.com/v0/{base_id}/Drivers"
    fields = {
        "Driver ID": driver.driverId,
        "Name": driver.name,
        "Phone": driver.phone,
        "Vehicle": driver.vehicle,
        "Plate Number": driver.plateNumber,
        "Status": driver.status,
        "Verification Status": driver.verificationStatus,
        "Completed Rides": driver.completedRides,
        "Cancelled Rides": driver.cancelledRides,
        "Notes": driver.notes,
        "Created At": datetime.now(timezone.utc).isoformat(),
    }
    response = await airtable_request(
        "POST", url, api_key, json={"fields": fields}
    )
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Airtable rejected the driver request.",
        )
    return {
        "message": "Driver created",
        "driverId": driver.driverId,
        "airtableRecordId": response.json().get("id"),
    }


@app.patch("/api/drivers/{driver_id}")
async def update_driver(
    driver: DriverUpdate,
    driver_id: str = Path(min_length=1),
    _: str = Depends(require_dispatcher_session),
):
    api_key, base_id, _ = airtable_settings()
    fields = {}
    if driver.status is not None:
        fields["Status"] = driver.status
    if driver.verificationStatus is not None:
        fields["Verification Status"] = driver.verificationStatus
    if driver.notes is not None:
        fields["Notes"] = driver.notes
    if not fields:
        raise HTTPException(
            status_code=422,
            detail="At least one driver field must be provided.",
        )

    url = f"https://api.airtable.com/v0/{base_id}/Drivers/{driver_id}"
    response = await airtable_request("PATCH", url, api_key, json={"fields": fields})
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Airtable rejected the driver update.",
        )
    return {"message": "Driver updated", "airtableRecordId": driver_id}


@app.post("/api/rides/{ride_id}/assign")
async def assign_driver(
    driver_id: str,
    ride_id: str = Path(min_length=1),
    _: str = Depends(require_dispatcher_session),
):
    api_key, base_id, table_name = airtable_settings()
    driver_url = f"https://api.airtable.com/v0/{base_id}/Drivers/{driver_id}"
    driver_response = await airtable_request("GET", driver_url, api_key)
    if driver_response.is_error:
        raise HTTPException(status_code=404, detail="Driver not found.")

    driver_fields = driver_response.json().get("fields", {})
    if (
        driver_fields.get("Status") != "ACTIVE"
        or driver_fields.get("Verification Status") != "VERIFIED"
    ):
        raise HTTPException(
            status_code=409,
            detail="Only ACTIVE and VERIFIED drivers are eligible for assignment.",
        )

    ride_url = f"https://api.airtable.com/v0/{base_id}/{table_name}/{ride_id}"
    ride_response = await airtable_request("GET", ride_url, api_key)
    if ride_response.is_error:
        raise HTTPException(status_code=404, detail="Ride not found.")
    if ride_response.json().get("fields", {}).get("Status") != "REQUESTED":
        raise HTTPException(
            status_code=409,
            detail="Only REQUESTED rides can be assigned.",
        )

    fields = {
        "Status": "DRIVER ASSIGNED",
        "Driver ID": driver_fields.get("Driver ID"),
        "Driver Name": driver_fields.get("Name"),
        "Driver Phone": driver_fields.get("Phone"),
        "Driver Vehicle": driver_fields.get("Vehicle"),
        "Driver Plate Number": driver_fields.get("Plate Number"),
    }
    response = await airtable_request(
        "PATCH", ride_url, api_key, json={"fields": fields}
    )
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Airtable rejected the ride assignment.",
        )
    return {
        "message": "Driver assigned",
        "rideId": ride_id,
        "driverId": driver_fields.get("Driver ID"),
        "status": "DRIVER ASSIGNED",
    }


@app.patch("/api/rides/{ride_id}/fare")
async def confirm_fare(
    fare_update: FareUpdate,
    ride_id: str = Path(min_length=1),
    _: str = Depends(require_dispatcher_session),
):
    api_key, base_id, table_name = airtable_settings()
    ride_url = f"https://api.airtable.com/v0/{base_id}/{table_name}/{ride_id}"
    ride_response = await airtable_request("GET", ride_url, api_key)
    if ride_response.is_error:
        raise HTTPException(status_code=404, detail="Ride not found.")
    if ride_response.json().get("fields", {}).get("Status") != "DRIVER ASSIGNED":
        raise HTTPException(
            status_code=409,
            detail="Fare can only be confirmed for DRIVER ASSIGNED rides.",
        )
    if not fare_update.fareConfirmed:
        raise HTTPException(
            status_code=422,
            detail="Fare must be confirmed before payment can proceed.",
        )

    response = await airtable_request(
        "PATCH",
        ride_url,
        api_key,
        json={
            "fields": {
                "Fare": fare_update.fare,
                "Fare Confirmed": True,
                "Payment Status": "PENDING",
            }
        },
    )
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Airtable rejected the fare confirmation.",
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
    api_key, base_id, table_name = airtable_settings()
    ride_url = f"https://api.airtable.com/v0/{base_id}/{table_name}/{ride_id}"
    ride_response = await airtable_request("GET", ride_url, api_key)
    if ride_response.is_error:
        raise HTTPException(status_code=404, detail="Ride not found.")

    ride_fields = ride_response.json().get("fields", {})
    if ride_fields.get("Status") != "DRIVER ASSIGNED":
        raise HTTPException(
            status_code=409,
            detail="Payment can only be verified for DRIVER ASSIGNED rides.",
        )
    if not ride_fields.get("Fare") or ride_fields.get("Fare Confirmed") is not True:
        raise HTTPException(
            status_code=409,
            detail="Payment requires a confirmed fare.",
        )
    if payment.paymentStatus == "PAID" and not payment.paymentReference.strip():
        raise HTTPException(
            status_code=422,
            detail="Payment reference is required for PAID payments.",
        )

    fields = {"Payment Status": payment.paymentStatus}
    if payment.paymentStatus == "PAID":
        fields.update(
            {
                "Payment Reference": payment.paymentReference.strip(),
                "Payment Method": payment.paymentMethod,
                "Status": "PAID",
            }
        )
    response = await airtable_request(
        "PATCH",
        ride_url,
        api_key,
        json={"fields": fields},
    )
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="Airtable rejected the payment update.",
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
    api_key, base_id, table_name = airtable_settings()
    ride_url = f"https://api.airtable.com/v0/{base_id}/{table_name}/{ride_id}"
    ride_response = await airtable_request("GET", ride_url, api_key)
    if ride_response.is_error:
        raise HTTPException(status_code=404, detail="Ride not found.")

    ride_fields = ride_response.json().get("fields", {})
    current_status = ride_fields.get("Status")
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

    assigned_driver_id = ride_fields.get("Driver ID")
    changes_driver_counter = status_update.status in {"COMPLETED", "CANCELLED"}
    is_requested_cancellation = (
        status_update.status == "CANCELLED" and current_status == "REQUESTED"
    )
    driver_record = None
    old_counter = None
    new_counter = None

    if changes_driver_counter and not is_requested_cancellation:
        if not assigned_driver_id:
            raise HTTPException(
                status_code=409,
                detail="This transition requires an assigned driver.",
            )
        driver_record = await get_assigned_driver(
            base_id, assigned_driver_id, api_key
        )
        driver_fields = driver_record.get("fields", {})
        counter_field = (
            "Completed Rides"
            if status_update.status == "COMPLETED"
            else "Cancelled Rides"
        )
        old_counter = driver_fields.get(counter_field, 0)
        if not isinstance(old_counter, (int, float)) or isinstance(old_counter, bool):
            raise HTTPException(
                status_code=502,
                detail=f"Assigned driver's {counter_field} value is invalid.",
            )
        new_counter = old_counter + 1
        counter_url = (
            f"https://api.airtable.com/v0/{base_id}/Drivers/"
            f"{driver_record['id']}"
        )
        counter_response = await airtable_request(
            "PATCH",
            counter_url,
            api_key,
            json={"fields": {counter_field: new_counter}},
        )
        if counter_response.is_error:
            raise HTTPException(
                status_code=502,
                detail=f"Airtable rejected the driver's {counter_field} update.",
            )

    ride_update_response = await airtable_request(
        "PATCH",
        ride_url,
        api_key,
        json={"fields": {"Status": status_update.status}},
    )
    if ride_update_response.is_error:
        if driver_record is not None and old_counter is not None:
            rollback_url = (
                f"https://api.airtable.com/v0/{base_id}/Drivers/"
                f"{driver_record['id']}"
            )
            rollback_response = await airtable_request(
                "PATCH",
                rollback_url,
                api_key,
                json={
                    "fields": {
                        "Completed Rides"
                        if status_update.status == "COMPLETED"
                        else "Cancelled Rides": old_counter
                    }
                },
            )
            if rollback_response.is_error:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Airtable rejected the ride status update, and the "
                        "driver counter rollback also failed."
                    ),
                )
        raise HTTPException(
            status_code=502,
            detail="Airtable rejected the ride status update.",
        )

    return {
        "success": True,
        "rideId": ride_id,
        "previousStatus": current_status,
        "status": status_update.status,
    }
