-- PostgreSQL 17 regression smoke tests for the payout migration.
-- Intended for an isolated GitHub Actions service container; NEVER run against production.
begin;
create role anon nologin;
create role authenticated nologin;
create role service_role nologin;

create table public.oka_rides_rides (
  id uuid primary key default gen_random_uuid(),
  reference text not null,
  status text not null,
  payment_status text not null,
  payment_method text,
  payment_reference text,
  driver_id text,
  driver_name text,
  fare numeric
);

\ir ../supabase/migrations/20261021190000_create_driver_earnings_ledger.sql

insert into public.oka_rides_rides
  (reference,status,payment_status,payment_method,payment_reference,driver_id,driver_name,fare)
values
  ('TEST-OPAY','PICKED UP','PAID','OPAY','OFFLINE-PAID-1','DRV-1','Driver Test',200.00),
  ('TEST-PAYSTACK','PICKED UP','PAID','PAYSTACK','PAYSTACK-PAID-1','DRV-1','Driver Test',100.00),
  ('TEST-UNPAID','PICKED UP','PENDING',null,null,'DRV-1','Driver Test',500.00);

-- Nothing is earned before the completed-trip boundary.
do $$
begin
  if (select count(*) from public.oka_rides_driver_earnings) <> 0 then
    raise exception 'Earnings were recorded before completion';
  end if;
end;
$$;

update public.oka_rides_rides
  set status='COMPLETED'
  where reference in ('TEST-OPAY','TEST-PAYSTACK','TEST-UNPAID');

do $$
declare
  v_total integer;
  v_unpaid integer;
  v_split integer;
begin
  select count(*), count(*) filter (where status='UNPAID'),
         count(*) filter (where status='PROVIDER_SPLIT')
    into v_total,v_unpaid,v_split
    from public.oka_rides_driver_earnings;
  if v_total <> 2 or v_unpaid <> 1 or v_split <> 1 then
    raise exception 'Completed ride classification wrong: %, %, %',v_total,v_unpaid,v_split;
  end if;
  if not exists (
    select 1 from public.oka_rides_driver_earnings
     where ride_reference='TEST-OPAY' and fare_kobo=20000
       and driver_earning_kobo=17000 and platform_commission_kobo=3000
  ) then
    raise exception 'Offline 85/15 allocation incorrect';
  end if;
  if not exists (
    select 1 from public.oka_rides_driver_earnings
     where ride_reference='TEST-PAYSTACK' and fare_kobo=10000
       and driver_earning_kobo=8500 and platform_commission_kobo=1500
  ) then
    raise exception 'Paystack 85/15 allocation incorrect';
  end if;
end;
$$;

-- A repeated status write cannot double count the ride.
update public.oka_rides_rides set status='COMPLETED' where reference='TEST-OPAY';
do $$
begin
  if (select count(*) from public.oka_rides_driver_earnings) <> 2 then
    raise exception 'Duplicate ride earning';
  end if;
end;
$$;

-- A stale dashboard amount must reject settlement and preserve unpaid rows.
do $$
begin
  begin
    perform public.oka_rides_settle_driver_day(
      'DRV-1',(now() at time zone 'Africa/Lagos')::date,
      'BANK-TRANSFER-1',1,17001::bigint,'dispatcher'
    );
    raise exception 'Expected stale-total rejection not raised';
  exception when others then
    if sqlerrm='Expected stale-total rejection not raised' then raise; end if;
  end;
  if not exists (
    select 1 from public.oka_rides_driver_earnings
     where ride_reference='TEST-OPAY' and status='UNPAID'
  ) then
    raise exception 'Stale-total request incorrectly settled a ride';
  end if;
end;
$$;

do $$
declare
  v_result jsonb;
begin
  v_result := public.oka_rides_settle_driver_day(
    'DRV-1',(now() at time zone 'Africa/Lagos')::date,
    'BANK-TRANSFER-1',1,17000::bigint,'dispatcher'
  );
  if v_result->>'success' <> 'true'
     or (v_result->>'rideCount')::integer <> 1
     or (v_result->>'amountKobo')::bigint <> 17000 then
    raise exception 'Unexpected settlement response: %',v_result;
  end if;
  if (select status from public.oka_rides_driver_earnings where ride_reference='TEST-PAYSTACK')
       <> 'PROVIDER_SPLIT' then
    raise exception 'Paystack split improperly included in manual payout';
  end if;
  if (select payout_reference from public.oka_rides_driver_earnings where ride_reference='TEST-OPAY')
       <> 'BANK-TRANSFER-1' then
    raise exception 'Bank transfer not recorded';
  end if;
  begin
    perform public.oka_rides_settle_driver_day(
      'DRV-1',(now() at time zone 'Africa/Lagos')::date,
      'BANK-TRANSFER-1',1,17000::bigint,'dispatcher'
    );
    raise exception 'Duplicate reference accepted';
  exception when others then
    if sqlerrm='Duplicate reference accepted' then raise; end if;
  end;
end;
$$;

-- Direct browser roles must not be able to read/modify the ledger or settle.
do $$
begin
  if has_table_privilege('anon','public.oka_rides_driver_earnings','SELECT')
     or has_table_privilege('authenticated','public.oka_rides_driver_earnings','UPDATE')
     or has_function_privilege(
       'anon','public.oka_rides_settle_driver_day(text,date,text,integer,bigint,text)','EXECUTE'
     ) then
    raise exception 'Public role received payout-ledger access';
  end if;
end;
$$;
rollback;
