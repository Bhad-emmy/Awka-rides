create table if not exists public.oka_rides_customers (
  id uuid primary key default gen_random_uuid(),
  full_name text not null,
  phone text not null unique,
  email text not null unique,
  password_hash text not null,
  created_at timestamptz not null default now()
);

alter table public.oka_rides_rides
  add column if not exists customer_id uuid;

alter table public.oka_rides_rides
  drop constraint if exists oka_rides_rides_customer_fk;

alter table public.oka_rides_rides
  add constraint oka_rides_rides_customer_fk
  foreign key (customer_id)
  references public.oka_rides_customers(id);

create index if not exists idx_oka_rides_rides_customer_id
  on public.oka_rides_rides(customer_id);

alter table public.oka_rides_customers enable row level security;