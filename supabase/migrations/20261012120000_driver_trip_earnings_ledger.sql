-- Driver trip earnings ledger and payout reconciliation for Awka Rides.
-- Apply this migration in the Supabase SQL Editor before enabling the UI.
create table if not exists public.oka_rides_driver_earnings (
  id uuid primary key default gen_random_uuid(),
  ride_id text not null unique,
  ride_reference text not null,
  driver_id text not null,
  driver_name text not null,
  service_date date not null,
  fare_kobo bigint not null check (fare_kobo > 0),
  driver_earnings_kobo bigint not null check (driver_earnings_kobo >= 0),
  platform_commission_kobo bigint not null check (platform_commission_kobo >= 0),
  payment_reference text not null,
  payment_method text not null check (payment_method in ('PAYSTACK','OFFLINE')),
  payment_status text not null default 'PAID' check (payment_status = 'PAID'),
  payout_status text not null default 'UNPAID' check (payout_status in ('UNPAID','PAID')),
  payout_reference text,
  payout_note text,
  paid_out_at timestamptz,
  paid_out_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint oka_rides_driver_earnings_split_matches check (driver_earnings_kobo + platform_commission_kobo = fare_kobo)
);
create index if not exists oka_rides_driver_earnings_service_date_idx
  on public.oka_rides_driver_earnings (service_date desc, driver_id);
create index if not exists oka_rides_driver_earnings_payout_status_idx
  on public.oka_rides_driver_earnings (payout_status, service_date desc);
create index if not exists oka_rides_driver_earnings_driver_date_idx
  on public.oka_rides_driver_earnings (driver_id, service_date desc);
alter table public.oka_rides_driver_earnings enable row level security;
