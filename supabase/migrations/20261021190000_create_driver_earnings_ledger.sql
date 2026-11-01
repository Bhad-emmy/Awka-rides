-- Safe, additive driver daily-earnings ledger.
-- Deployment order: apply this migration, verify access, then deploy matching backend.
-- Existing completed rides are NOT backfilled: historical records may be test rides.
-- This migration does not change OrderPing, Auth, payment collection, or live transfers.
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
  payment_method text not null check (payment_method in ('PAYSTACK','OFFLINE')),
  payment_reference text not null,
  earning_date date not null,
  status text not null check (status in ('UNPAID','PAID_OUT','PROVIDER_SPLIT')),
  paid_at timestamptz not null default now(),
  payout_reference text,
  paid_out_at timestamptz,
  paid_out_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint oka_driver_split_math check
    (driver_earning_kobo + platform_commission_kobo = fare_kobo),
  constraint oka_driver_method_status check
    ((payment_method = 'PAYSTACK' and status = 'PROVIDER_SPLIT')
      or (payment_method = 'OFFLINE' and status in ('UNPAID','PAID_OUT'))),
  constraint oka_driver_payout_metadata check
    ((status <> 'PAID_OUT' and payout_reference is null and paid_out_at is null and paid_out_by is null)
      or (status = 'PAID_OUT' and nullif(btrim(payout_reference), '') is not null
            and paid_out_at is not null and nullif(btrim(paid_out_by), '') is not null))
);
create index if not exists oka_driver_earnings_date_driver_idx
  on public.oka_rides_driver_earnings (earning_date desc, driver_id, status);
create index if not exists oka_driver_earnings_payment_reference_idx
  on public.oka_rides_driver_earnings (payment_reference);
create index if not exists oka_driver_earnings_payout_reference_idx
  on public.oka_rides_driver_earnings (payout_reference)
  where payout_reference is not null;
alter table public.oka_rides_driver_earnings enable row level security;
revoke all on public.oka_rides_driver_earnings from public, anon, authenticated;
grant select, insert, update on public.oka_rides_driver_earnings to service_role;

-- Insert the earning atomically when a paid ride transitions to COMPLETED.
-- Paystack transactions use a configured 85/15 subaccount split at checkout;
-- mark them PROVIDER_SPLIT, never eligible for a second manual payout.
-- Manual OPay/offline fares become UNPAID until the bank transfer is recorded.
create or replace function public.oka_rides_record_completed_driver_earning()
returns trigger language plpgsql security invoker set search_path = ''
as $$
declare
  v_fare_kobo bigint;
  v_driver_kobo bigint;
  v_payment_method text;
begin
  if new.status <> 'COMPLETED'
     or old.status is not distinct from new.status
     or new.payment_status <> 'PAID'
     or new.driver_id is null
     or new.payment_reference is null
     or nullif(btrim(new.payment_reference), '') is null
     or new.payment_method not in ('PAYSTACK','OPAY')
     or new.fare is null
     or new.fare <= 0 then
    return new;
  end if;
  v_fare_kobo := round(new.fare * 100)::bigint;
  if v_fare_kobo <= 0 then
    return new;
  end if;
  v_driver_kobo := (v_fare_kobo * 85 + 50) / 100;
  v_payment_method := case when new.payment_method = 'PAYSTACK' then 'PAYSTACK' else 'OFFLINE' end;
  insert into public.oka_rides_driver_earnings (
    ride_id, ride_reference, driver_id, driver_name,
    fare_kobo, driver_earning_kobo, platform_commission_kobo,
    payment_method, payment_reference, earning_date, status
  ) values (
    new.id::text, new.reference, new.driver_id, coalesce(new.driver_name, new.driver_id),
    v_fare_kobo, v_driver_kobo, v_fare_kobo - v_driver_kobo,
    v_payment_method, new.payment_reference,
    (now() at time zone 'Africa/Lagos')::date,
    case when v_payment_method = 'PAYSTACK' then 'PROVIDER_SPLIT' else 'UNPAID' end
  ) on conflict (ride_id) do nothing;
  return new;
end;
$$;
revoke all on function public.oka_rides_record_completed_driver_earning() from public, anon, authenticated;
drop trigger if exists oka_rides_record_completed_driver_earning on public.oka_rides_rides;
create trigger oka_rides_record_completed_driver_earning
after update of status on public.oka_rides_rides
for each row execute function public.oka_rides_record_completed_driver_earning();

-- A single transaction locks the exact day's unpaid entries and compares the
-- dispatcher-confirmed count/amount before recording the bank-transfer reference.
-- Reusing a reference is rejected, including concurrent requests.
create or replace function public.oka_rides_settle_driver_day(
  p_driver_id text,
  p_earning_date date,
  p_reference text,
  p_expected_count integer,
  p_expected_kobo bigint,
  p_dispatcher_id text
) returns jsonb language plpgsql security invoker set search_path = ''
as $$
declare
  v_reference text := btrim(coalesce(p_reference, ''));
  v_count integer;
  v_amount bigint;
  v_changed integer;
begin
  if nullif(btrim(coalesce(p_driver_id,'')), '') is null
     or nullif(v_reference,'') is null
     or nullif(btrim(coalesce(p_dispatcher_id,'')), '') is null
     or p_earning_date is null
     or p_expected_count <= 0
     or p_expected_kobo <= 0 then
    raise exception 'Invalid driver payout confirmation details';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oka-rides-payout:' || v_reference, 0));
  if exists (select 1 from public.oka_rides_driver_earnings where payout_reference = v_reference) then
    raise exception 'This bank transfer reference has already been recorded';
  end if;
  select count(*)::integer, coalesce(sum(locked.driver_earning_kobo), 0)::bigint
    into v_count, v_amount
    from (
      select driver_earning_kobo from public.oka_rides_driver_earnings
       where driver_id = p_driver_id and earning_date = p_earning_date and status = 'UNPAID'
       for update
    ) locked;
  if v_count <> p_expected_count or v_amount <> p_expected_kobo then
    raise exception 'Daily payout total changed. Refresh the dashboard before recording a transfer';
  end if;
  update public.oka_rides_driver_earnings
     set status = 'PAID_OUT', payout_reference = v_reference,
         paid_out_at = now(), paid_out_by = p_dispatcher_id, updated_at = now()
   where driver_id = p_driver_id and earning_date = p_earning_date and status = 'UNPAID';
  get diagnostics v_changed = row_count;
  if v_changed <> v_count then
    raise exception 'Payout reconciliation conflict. No changes were saved';
  end if;
  return jsonb_build_object(
    'success', true, 'driverId', p_driver_id, 'earningDate', p_earning_date,
    'rideCount', v_count, 'amountKobo', v_amount, 'payoutReference', v_reference
  );
end;
$$;
revoke all on function public.oka_rides_settle_driver_day(text,date,text,integer,bigint,text)
  from public, anon, authenticated;
grant execute on function public.oka_rides_settle_driver_day(text,date,text,integer,bigint,text)
  to service_role;
