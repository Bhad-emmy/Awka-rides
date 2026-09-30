-- Keep the production schema reproducible.
-- These fields were introduced during the manual-dispatch implementation.

alter table public.oka_rides_drivers
  add column if not exists availability_status text not null default 'OFFLINE',
  add column if not exists current_latitude double precision,
  add column if not exists current_longitude double precision,
  add column if not exists last_location_at timestamptz;

alter table public.oka_rides_drivers
  drop constraint if exists oka_rides_drivers_availability_status_check;

alter table public.oka_rides_drivers
  add constraint oka_rides_drivers_availability_status_check
  check (availability_status in ('ONLINE','OFFLINE','BUSY'));

create index if not exists oka_rides_drivers_dispatch_idx
  on public.oka_rides_drivers (availability_status, status, verification_status);

alter table public.oka_rides_rides
  add column if not exists dispatch_attempts integer not null default 0,
  add column if not exists dispatched_at timestamptz;

-- Google/OAuth customers do not have a local password.
alter table public.oka_rides_customers
  alter column password_hash drop not null;
