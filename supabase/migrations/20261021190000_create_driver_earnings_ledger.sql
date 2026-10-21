-- Driver earnings ledger for end-of-day manual payouts.
-- Apply in Supabase SQL Editor before deploying code that uses this table.
create table if not exists public.oka_rides_driver_earnings (
    id uuid primary key default gen_random_uuid(),
    ride_id text not null unique,
    ride_reference text not null,
    driver_id text not null,
    driver_name text not null default '',
    fare_kobo bigint not null check (fare_kobo > 0),
    driver_earning_kobo bigint not null check (driver_earning_kobo > 0),
    platform_commission_kobo bigint not null check (platform_commission_kobo >= 0),
    currency text not null default 'NGN' check (currency = 'NGN'),
    payment_method text not null check (payment_method in ('PAYSTACK', 'OFFLINE')),
    payment_reference text not null,
    earning_date date not null,
    status text not null default 'UNPAID' check (status in ('UNPAID', 'PAID_OUT')),
    paid_at timestamptz not null default now(),
    payout_reference text,
    paid_out_at timestamptz,
    paid_out_by text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (driver_earning_kobo + platform_commission_kobo = fare_kobo),
    check (
        (status = 'UNPAID' and payout_reference is null and paid_out_at is null)
        or
        (status = 'PAID_OUT' and payout_reference is not null and paid_out_at is not null)
    )
);

create index if not exists oka_rides_driver_earnings_date_driver_idx
    on public.oka_rides_driver_earnings (earning_date, driver_id, status);
create index if not exists oka_rides_driver_earnings_paid_at_idx
    on public.oka_rides_driver_earnings (paid_at desc);

alter table public.oka_rides_driver_earnings enable row level security;
