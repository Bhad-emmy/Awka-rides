create table if not exists public.oka_rides_password_reset_tokens (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references public.oka_rides_customers(id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists idx_oka_rides_password_reset_tokens_customer
  on public.oka_rides_password_reset_tokens(customer_id);

create index if not exists idx_oka_rides_password_reset_tokens_expiry
  on public.oka_rides_password_reset_tokens(expires_at);

alter table public.oka_rides_password_reset_tokens enable row level security;

revoke all on public.oka_rides_password_reset_tokens from anon, authenticated;

create or replace function public.oka_rides_consume_password_reset_token(p_token_hash text)
returns table(customer_id uuid)
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  update public.oka_rides_password_reset_tokens
  set used_at = now()
  where token_hash = p_token_hash
    and used_at is null
    and expires_at > now()
  returning oka_rides_password_reset_tokens.customer_id;
end;
$$;

revoke all on function public.oka_rides_consume_password_reset_token(text) from public, anon, authenticated;
grant execute on function public.oka_rides_consume_password_reset_token(text) to service_role;
