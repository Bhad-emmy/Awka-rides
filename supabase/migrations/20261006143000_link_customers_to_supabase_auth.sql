-- Link application customer records to Supabase Auth users.
alter table public.oka_rides_customers
  add column if not exists auth_user_id uuid references auth.users(id) on delete set null;

create unique index if not exists idx_oka_rides_customers_auth_user_id
  on public.oka_rides_customers(auth_user_id)
  where auth_user_id is not null;

-- Existing Google-authenticated customers are linked by their verified email.
update public.oka_rides_customers c
set auth_user_id = u.id,
    password_hash = null
from auth.users u
where c.auth_user_id is null
  and lower(c.email) = lower(u.email)
  and exists (
    select 1
    from auth.identities i
    where i.user_id = u.id
      and i.provider = 'google'
  );