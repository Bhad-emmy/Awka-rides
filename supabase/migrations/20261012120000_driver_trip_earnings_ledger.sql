-- Driver trip earnings ledger and payout reconciliation for Awka Rides.
-- Apply this migration in the Supabase SQL Editor before enabling the UI.
create table if not exists public.oka_rides_driver_earnings (
  id uuid primary key default gen_random_uuid(),
  ride_id text not null unique,
  ride_reference text not null,
  driver_id text not null,
  driver_name text not null,
  route_text text not null,
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


-- Create the earning only when a paid trip transitions to COMPLETED. This
-- runs for both driver and dispatcher status changes and is protected by the
-- unique ride_id constraint, so retries cannot create duplicate earnings.
create or replace function public.oka_rides_record_completed_driver_earning()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_fare_kobo bigint;
  v_driver_kobo bigint;
  v_service_date date;
begin
  if new.status = 'COMPLETED'
     and old.status is distinct from new.status
     and new.payment_status = 'PAID'
     and new.driver_id is not null
     and new.fare is not null
     and new.fare > 0 then
    v_fare_kobo := round(new.fare * 100)::bigint;
    v_driver_kobo := round(v_fare_kobo * 0.85)::bigint;
    v_service_date := (now() at time zone 'Africa/Lagos')::date;

    insert into public.oka_rides_driver_earnings (
      ride_id, ride_reference, driver_id, driver_name, route_text,
      service_date, fare_kobo, driver_earnings_kobo, platform_commission_kobo,
      payment_reference, payment_method, payment_status, payout_status
    ) values (
      new.id::text,
      coalesce(new.reference, ''),
      new.driver_id,
      coalesce(new.driver_name, new.driver_id),
      coalesce(new.pickup, 'Unknown pickup') || ' → ' || coalesce(new.destination, 'Unknown destination'),
      v_service_date,
      v_fare_kobo,
      v_driver_kobo,
      v_fare_kobo - v_driver_kobo,
      coalesce(new.payment_reference, 'UNKNOWN-' || new.reference),
      case when new.payment_method = 'PAYSTACK' then 'PAYSTACK' else 'OFFLINE' end,
      'PAID',
      'UNPAID'
    )
    on conflict (ride_id) do nothing;
  end if;
  return new;
end;
$$;

drop trigger if exists oka_rides_record_completed_driver_earning
  on public.oka_rides_rides;
create trigger oka_rides_record_completed_driver_earning
after update of status on public.oka_rides_rides
for each row
execute function public.oka_rides_record_completed_driver_earning();
