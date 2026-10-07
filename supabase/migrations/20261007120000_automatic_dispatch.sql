alter table public.oka_rides_drivers
  add column if not exists pin_hash text;

alter table public.oka_rides_rides
  add column if not exists dispatch_offer_status text not null default 'NONE'
    check (dispatch_offer_status in ('NONE', 'OFFERED', 'ACCEPTED')),
  add column if not exists dispatch_offer_expires_at timestamptz;

create index if not exists oka_rides_rides_dispatch_offer_idx
  on public.oka_rides_rides (status, dispatch_offer_status, driver_id);

create index if not exists oka_rides_drivers_dispatch_idx
  on public.oka_rides_drivers (status, verification_status, availability_status);
