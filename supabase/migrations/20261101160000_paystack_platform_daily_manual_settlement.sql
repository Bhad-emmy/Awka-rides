-- Cutover: new Paystack charges are collected by Oka Rides for manual driver
-- settlement. Existing Paystack checkouts are deliberately treated as legacy
-- split-possible until the operator reconciles them with Paystack statements.
-- This migration does not backfill or pay historical records.
alter table public.oka_rides_rides
  add column if not exists paystack_settlement_mode text;
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid='public.oka_rides_rides'::regclass
      and conname='oka_rides_paystack_settlement_mode_check'
  ) then
    alter table public.oka_rides_rides
      add constraint oka_rides_paystack_settlement_mode_check
      check (paystack_settlement_mode is null or paystack_settlement_mode = 'MANUAL');
  end if;
end;
$$;
-- Verified manual bank transfer destination, visible only to signed-in
-- dispatchers via protected APIs. Oka drivers table is RLS-protected and
-- inaccessible to browser anon/authenticated DB roles.
alter table public.oka_rides_drivers
  add column if not exists payout_bank_code text,
  add column if not exists payout_bank_name text,
  add column if not exists payout_account_number text,
  add column if not exists payout_account_name text,
  add column if not exists payout_bank_verified_at timestamptz;

-- PAYSTACK + MANUAL => UNPAID. Existing PROVIDER_SPLIT entries are retained
-- for independent investigation; never automatically recast them as due.
alter table public.oka_rides_driver_earnings
  drop constraint if exists oka_driver_method_status;
alter table public.oka_rides_driver_earnings
  add constraint oka_driver_method_status
  check (
    (payment_method in ('PAYSTACK','OFFLINE') and status in ('UNPAID','PAID_OUT'))
    or (payment_method = 'PAYSTACK' and status = 'PROVIDER_SPLIT')
  );

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
    case when v_payment_method = 'PAYSTACK' and new.paystack_settlement_mode is distinct from 'MANUAL' then 'PROVIDER_SPLIT' else 'UNPAID' end
  ) on conflict (ride_id) do nothing;
  return new;
end;
$$;
revoke all on function public.oka_rides_record_completed_driver_earning()
  from public, anon, authenticated;

-- Existing trigger continues invoking the redefined function.
