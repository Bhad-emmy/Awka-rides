drop index if exists public.oka_rides_customer_sessions_csrf_token_hash_idx;
alter table public.oka_rides_customer_sessions
  drop column if exists csrf_token_hash;
