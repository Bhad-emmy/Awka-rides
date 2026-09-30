alter table public.oka_rides_customer_sessions
  add column if not exists csrf_token_hash text;

create index if not exists oka_rides_customer_sessions_csrf_token_hash_idx
  on public.oka_rides_customer_sessions (csrf_token_hash);

revoke all on public.oka_rides_customer_sessions from anon, authenticated;
