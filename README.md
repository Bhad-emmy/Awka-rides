# Ọka-Rides

> A web-based Keke dispatch and ride-booking platform for Awka, Anambra State, Nigeria.

Ọka-Rides connects customers who need rides with a dispatcher who manages incoming requests, assigns verified drivers, confirms fares and payments, and tracks the ride through its lifecycle.

**Production:** https://oka-rides.vercel.app/

## What Ọka-Rides Does

The current V1 workflow is intentionally dispatcher-led:

1. A customer submits a ride request.
2. The request is stored in the MEKA LOGIC Supabase project.
3. The dispatcher dashboard detects a new `REQUESTED` ride.
4. The dispatcher reviews the pickup, destination, passenger details, timing, and map coordinates.
5. The dispatcher assigns an eligible driver.
6. The assigned driver receives an SMS through Termii.
7. The dispatcher confirms the fare.
8. The customer pays through the official Ọka-Rides OPay account.
9. The dispatcher verifies the payment and advances the ride.
10. The ride moves through the operational lifecycle until completion or cancellation.

## Core Features

### Customer booking
- Customer ride request form
- Pickup and destination capture
- Optional pickup/destination coordinates
- Passenger count
- Pickup timing and scheduled time
- Customer phone and email collection
- Booking/reference generation
- Customer ride status/account flow

### Dispatcher operations
- Protected dispatcher login
- Incoming ride queue
- Ride status filtering
- Ride detail view
- Pickup/destination map
- Eligible driver selection
- Driver assignment
- Fare confirmation
- Manual payment verification
- Ride lifecycle controls
- Automatic new-ride alerting
- Browser notification support
- Repeating alert beep for new bookings

### Driver notification
When a driver is assigned, the backend can send an SMS through Termii.

The current assignment SMS includes:
- Ride reference
- Customer name
- Pickup
- Destination
- Fare when already available

### Ride lifecycle

`REQUESTED` → `DRIVER ASSIGNED` → `PAID` → `PICKED UP` → `COMPLETED`

A ride can also be cancelled from supported stages.

## Architecture

```
Customer
   |
   v
Vercel static frontend
   |
   v
FastAPI API
   |
   +--------------------+
   |                    |
   v                    v
Supabase             Termii
(MEKA LOGIC)         (Driver SMS)
   |
   +--> oka_rides_customers
   +--> oka_rides_drivers
   +--> oka_rides_rides
```

### Stack

| Layer | Technology |
|---|---|
| Frontend | HTML, CSS, JavaScript |
| Backend | Python, FastAPI |
| Database | Supabase / PostgreSQL |
| Hosting | Vercel |
| Maps | Leaflet + OpenStreetMap |
| Driver SMS | Termii |
| Payment | Manual OPay |
| Version control | Git + GitHub |

## Supabase

Ọka-Rides uses the **MEKA LOGIC** Supabase project.

Project reference:

```
baoqyhhyenimfhkrwspn
```

Relevant tables:

- `oka_rides_rides`
- `oka_rides_drivers`
- `oka_rides_customers`

The main ride table stores customer details, locations, booking status, driver assignment, fare, payment information, and coordinates.

## Dispatcher Notifications

The dispatcher dashboard checks for new `REQUESTED` rides automatically while the dispatcher page is open.

When a new ride is detected:

- An in-page alert appears.
- A repeating beep starts.
- Supported devices may vibrate.
- Browser/OS notifications can appear after permission is granted.
- **View ride** opens the booking directly.

To receive browser notifications, open the dispatcher and select:

**Enable alerts → Allow**

The browser must remain open for these client-side alerts.

## Driver SMS

Driver SMS uses Termii.

Environment variables:

```env
TERMII_API_KEY=
TERMII_SENDER_ID=OKA-RIDES
TERMII_BASE_URL=https://api.ng.termii.com/api
```

The Termii API key must remain server-side.

The current V1 implementation sends the SMS when a driver is assigned.

## Payment

The live V1 payment method is **manual OPay**.

Customers pay the official Ọka-Rides OPay account, and the dispatcher verifies the payment before advancing the ride.

Paystack code remains in the backend as dormant infrastructure for later use; it is not the current customer payment flow.

## Environment Variables

Create the backend environment from `backend/.env.example`.

Required production configuration includes:

```env
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=

FRONTEND_ORIGIN=

DISPATCHER_USERNAME=
DISPATCHER_PASSWORD_HASH=
DISPATCHER_SESSION_SECRET=
DISPATCHER_COOKIE_SECURE=true

TERMII_API_KEY=
TERMII_SENDER_ID=OKA-RIDES
TERMII_BASE_URL=https://api.ng.termii.com/api
```

Keep the Supabase service-role key, dispatcher secrets, and Termii API key out of the frontend and out of Git.

## Local Development

### 1. Clone the repository

```bash
git clone https://github.com/Bhad-emmy/Awka-rides.git
cd Awka-rides
```

### 2. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create:

```
backend/.env
```

using:

```
backend/.env.example
```

### 5. Run FastAPI locally

```bash
python -m uvicorn api.index:app --reload --port 8000
```

The API will be available at:

```
http://127.0.0.1:8000
```

## Deployment

The project is connected to Vercel through GitHub.

Main production domain:

```
https://oka-rides.vercel.app/
```

The deployment uses:

- `api/index.py` as the FastAPI entry point
- `vercel.json` for API/static routing
- GitHub `main` branch deployments

Production environment variables must be configured in Vercel.

## Security Notes

The project uses:

- Protected dispatcher endpoints
- HTTP-only dispatcher session cookies
- PBKDF2-SHA256 password verification
- Signed dispatcher sessions
- Configurable CORS
- Server-side Supabase service-role access
- Server-side Termii access
- Input validation through Pydantic
- Backend rate limiting
- Escaped dispatcher-rendered API data

### Important

Never commit:

```
backend/.env
```

or any API keys, service-role keys, dispatcher passwords, or session secrets.

## Testing

Before treating a deployment as production-ready, test at minimum:

1. Customer can create a ride.
2. Ride appears in the dispatcher queue.
3. New ride alert appears without a manual refresh.
4. Dispatcher can assign an ACTIVE + VERIFIED driver.
5. Driver SMS is sent when Termii is configured.
6. Fare can be confirmed.
7. Payment can be recorded.
8. Ride lifecycle transitions work correctly.
9. Unauthorized dispatcher requests are rejected.
10. Public customer pages cannot access protected dispatcher data.

## Current V1 Operational Model

Ọka-Rides is not an automated driver marketplace yet.

The dispatcher is the control point:

**Customer request → Dispatcher → Driver assignment → Payment verification → Ride operation**

This keeps the first version operationally simple while the driver network is being established.

## Repository Structure

```
.
├── api/
│   └── index.py
├── backend/
│   ├── main.py
│   ├── main_supabase.py
│   ├── rate_limit.py
│   └── .env.example
├── index.html
├── booking.html
├── success.html
├── dispatcher.html
├── dispatcher.js
├── script.js
├── style.css
├── requirements.txt
└── vercel.json
```

## Project Status

**V1:** Operational build in active testing and iteration.

Current focus is reliable booking, dispatcher awareness, driver assignment, payment verification, and driver notification before expanding the platform.

## License

No open-source license has been declared yet.
