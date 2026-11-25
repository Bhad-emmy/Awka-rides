-- Isolated PostgreSQL migration test. Never run against the shared production database.
create role anon nologin;
create role authenticated nologin;
create role service_role nologin;

create schema storage;
create table storage.buckets (
  id text primary key,
  name text not null,
  public boolean not null default false,
  file_size_limit bigint,
  allowed_mime_types text[]
);

create table public.oka_rides_drivers (
  driver_id text primary key,
  status text not null,
  verification_status text not null,
  availability_status text not null
);

insert into public.oka_rides_drivers
  (driver_id, status, verification_status, availability_status)
values ('legacy-driver-fixture', 'ACTIVE', 'VERIFIED', 'BUSY');

\ir ../supabase/migrations/20261125120000_oka_rides_driver_document_review.sql

do $$
declare
  legacy record;
  bucket record;
begin
  select status, verification_status, availability_status, document_review_required
    into legacy
    from public.oka_rides_drivers
   where driver_id = 'legacy-driver-fixture';

  if legacy.status <> 'ACTIVE'
     or legacy.verification_status <> 'VERIFIED'
     or legacy.availability_status <> 'BUSY'
     or legacy.document_review_required is distinct from false then
    raise exception 'Migration changed or failed to grandfather the existing driver';
  end if;

  if not (select relrowsecurity from pg_class where oid = 'public.oka_rides_driver_documents'::regclass) then
    raise exception 'Driver document table must have RLS enabled';
  end if;

  if has_table_privilege('anon', 'public.oka_rides_driver_documents', 'SELECT')
     or has_table_privilege('authenticated', 'public.oka_rides_driver_documents', 'SELECT')
     or not has_table_privilege('service_role', 'public.oka_rides_driver_documents', 'SELECT') then
    raise exception 'Driver document table grants are not service-role-only';
  end if;

  select b.public as is_public into bucket
    from storage.buckets b
   where b.id = 'oka-rides-driver-documents';

  if not found or bucket.is_public is distinct from false then
    raise exception 'Driver document storage bucket must be private';
  end if;
end
$$;
