-- Supabase Auth now owns customer password recovery.
drop function if exists public.oka_rides_consume_password_reset_token(text);
drop table if exists public.oka_rides_password_reset_tokens;