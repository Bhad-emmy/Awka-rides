-- Reproduce the production customer session store for fresh environments.
-- Customer session tokens are random opaque values; only their SHA-256 hashes
-- are persisted in this table.

create table if not exists public.oka_rides_customer_sessions (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references public.oka_rides_customers(id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  revoked_at timestamptz
);

create index if not exists oka_rides_customer_sessions_customer_idx
  on public.oka_rides_customer_sessions (customer_id, expires_at desc);

create index if not exists oka_rides_customer_sessions_expiry_idx
  on public.oka_rides_customer_sessions (expires_at);

alter table public.oka_rides_customer_sessions enable row level security;
