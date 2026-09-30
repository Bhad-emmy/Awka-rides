create or replace function public.oka_rides_check_rate_limit(
  p_rate_key text,
  p_limit integer,
  p_window_seconds integer
)
returns table(allowed boolean, retry_after integer)
language plpgsql
security definer
set search_path = ''
as $function$
declare
  cutoff_at timestamptz;
  oldest_at timestamptz;
  current_count integer;
  retry_seconds integer;
begin
  if p_rate_key is null or pg_catalog.length(p_rate_key) = 0
     or p_limit < 1 or p_window_seconds < 1 then
    raise exception 'Invalid rate limit parameters';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(p_rate_key), 0);

  cutoff_at := pg_catalog.now() - pg_catalog.make_interval(secs => p_window_seconds);

  delete from private.oka_rides_rate_limit_events
  where rate_key = p_rate_key
    and request_at <= cutoff_at;

  select pg_catalog.count(*), pg_catalog.min(request_at)
    into current_count, oldest_at
  from private.oka_rides_rate_limit_events
  where rate_key = p_rate_key
    and request_at > cutoff_at;

  if current_count >= p_limit then
    retry_seconds := pg_catalog.greatest(
      1,
      pg_catalog.ceil(
        extract(
          epoch from ((oldest_at + pg_catalog.make_interval(secs => p_window_seconds)) - pg_catalog.now())
        )
      )::integer
    );
    return query select false, retry_seconds;
    return;
  end if;

  insert into private.oka_rides_rate_limit_events(rate_key)
  values (p_rate_key);

  return query select true, 0;
end;
$function$;

revoke execute on function public.oka_rides_check_rate_limit(text, integer, integer) from public, anon, authenticated;
grant execute on function public.oka_rides_check_rate_limit(text, integer, integer) to service_role;
