-- Payment ledger for Paystack and dispatcher-verified offline payments.
-- Apply this migration in the Supabase SQL Editor before deploying the application.
create table if not exists public.oka_rides_payment_records (
    id uuid primary key default gen_random_uuid(),
    ride_id text not null,
    ride_reference text not null,
    reference text not null unique,
    amount_kobo bigint not null check (amount_kobo > 0),
    currency text not null default 'NGN' check (currency = 'NGN'),
    method text not null check (method in ('PAYSTACK', 'OFFLINE')),
    status text not null check (status in ('PENDING', 'PAID', 'FAILED')),
    provider text not null,
    provider_data jsonb not null default '{}'::jsonb,
    paid_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists oka_rides_payment_records_ride_id_created_at_idx
    on public.oka_rides_payment_records (ride_id, created_at desc);
create index if not exists oka_rides_payment_records_status_created_at_idx
    on public.oka_rides_payment_records (status, created_at desc);

-- Keep the ledger private; API access uses the backend service-role key.
alter table public.oka_rides_payment_records enable row level security;
