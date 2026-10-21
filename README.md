# Ọka-Rides

> A web-based Keke dispatch and ride-booking platform for Awka, Anambra State, Nigeria.

Ọka-Rides connects customers who need rides with a dispatcher who manages incoming requests, assigns verified drivers, confirms fares and payments, and tracks the ride through its lifecycle.

**Production:** https://www.okarides.com.ng/

## What Ọka-Rides Does

The current V1 workflow is intentionally dispatcher-led:

1. A customer submits a ride request.
2. The request is stored in the MEKA LOGIC Supabase project.
3. The dispatcher dashboard detects a new `REQUESTED` ride.
4. The dispatcher reviews the pickup, destination, passenger details, timing, and map coordinates.
5. The dispatcher assigns an eligible driver.
6. The assigned driver receives an SMS through Termii.
7. The dispatcher confirms the fare.
8. The customer pays through Paystack hosted checkout after the fare is confirmed.
9. The backend verifies the transaction directly with Paystack; a signed webhook provides a second confirmation path.
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
- Paystack hosted checkout and server-side verification
- Signed Paystack webhook handling with idempotent payment updates
- Manual dispatcher payment recording for supported offline payments
- Ride lifecycle controls
- Automatic new-ride alerting
- 5-second background refresh for customer ride lists, dispatcher queue/selected ride/payment history, driver offers/current ride, and payment records; data updates without reloading the page
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
| Payment | Paystack hosted checkout (requires a configured test/live secret key) |
| Version control | Git + GitHub |

## Paystack setup

Paystack checkout is implemented using the hosted checkout flow. The secret key is read only by the FastAPI backend; it must never be placed in frontend JavaScript or committed to Git.

Set these Vercel environment variables for the deployment you are testing:

- `PAYSTACK_SECRET_KEY`: Paystack **test** secret key (`sk_test_...`) for initial testing.
- `PAYSTACK_CALLBACK_URL` (optional): customer-facing callback origin. Production should use `https://www.okarides.com.ng`; if omitted, the API uses an allowed request origin.

No public key is required for this hosted-checkout implementation because the browser is redirected to Paystack's authorization URL. Add the webhook in Paystack **Settings → API Keys & Webhooks**:

```
https://www.okarides.com.ng/api/payments/paystack/webhook
```

The webhook validates `x-paystack-signature`, then verifies the transaction with Paystack before updating a ride. The amount must match the confirmed fare in NGN, and the transaction metadata must identify the same ride. Do not use live keys until test checkout, callback verification, and webhook tests pass.

## Payment records ledger

Verified Paystack payments and dispatcher-verified offline payments are stored in `oka_rides_payment_records`, with a unique payment reference, ride link, amount in kobo, currency, method, status, provider metadata, and timestamps. The dispatcher can review the ledger at `/payment-records.html`; each ride also displays its payment method, reference, and payment timestamp.

**Required before deploying the payment-ledger code:** run the SQL migration in `supabase/migrations/20261010090000_payment_records_ledger.sql` in the Supabase SQL Editor. The backend uses the service-role key; row-level security is enabled and no public table policies are added. Do not deploy this change before applying the migration.

Paystack webhook/callback confirmation remains server-verified. Repeated notifications use the transaction reference to avoid duplicate ledger rows. Manual dispatcher payment recording is explicitly treated as an offline payment and cannot overwrite a ride already marked as a Paystack payment.

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

## Driver earnings and end-of-day payouts

The dispatcher has a **Driver earnings** page at `/driver-earnings.html`. After a ride is marked PAID through Paystack verification or the dispatcher's offline-payment workflow, the backend records one earning entry for that ride. Duplicate callbacks do not create duplicate entries.

- The current earnings policy follows the existing Paystack split configuration: **85% of the fare to the driver and 15% platform commission**. Values are stored in kobo to avoid floating-point currency drift.
- The page summarizes each Nigeria business date (`Africa/Lagos`) by driver and shows gross fare, driver share, platform commission, paid-out amount, and outstanding amount.
- After the actual bank transfer, a dispatcher can record the payout reference to mark that driver's unpaid rides for the selected date as paid out. This is a ledger action only; it does **not** send money or initiate a bank transfer.
- Before deploying this feature, apply `supabase/migrations/20261021190000_create_driver_earnings_ledger.sql` in the Supabase SQL Editor. The migration enables RLS; access is intended to be through the backend service-role API only. Do not enable direct browser access to the table.

## Payment status and production readiness

Paystack hosted checkout is implemented in the customer ride flow. When a ride has an assigned driver, a confirmed fare, and an outstanding payment, the customer interface displays **Pay securely with Paystack**. The FastAPI backend initializes checkout, verifies transactions server-side, and handles signed Paystack webhooks. Offline payments can also be recorded by the dispatcher.

**Implementation does not mean production checkout is operational.** At the time of the latest configuration check, the Vercel Production `PAYSTACK_SECRET_KEY` variable exists but has an empty value. Therefore Paystack checkout/webhook verification cannot complete successfully in production until a valid key is configured. Do not expose the key in frontend code, README, logs, or Git. Configure a Paystack test secret key first, verify checkout/callback/webhook and payment-ledger behavior, and only then consider live mode.

Manual OPay remains an available offline payment path where the dispatcher records and verifies payment. The customer-facing Paystack button is part of the implemented flow; do not describe Paystack as dormant or removed.

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
https://www.okarides.com.ng/
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
11. Customer activity and previous rides update within 5 seconds without resetting the page or any form in progress.
12. Dispatcher ride status, driver offers, and payment records update without manually refreshing the page.

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
