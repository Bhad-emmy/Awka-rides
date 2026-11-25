-- Oka Rides only: private driver document storage and dispatcher review.
-- Existing driver status and availability are preserved. Only newly registered
-- drivers are gated by document_review_required.

alter table public.oka_rides_drivers
  add column if not exists document_review_required boolean not null default false;

create table if not exists public.oka_rides_driver_documents (
  id uuid primary key default gen_random_uuid(),
  driver_id text not null references public.oka_rides_drivers(driver_id) on delete restrict,
  document_type text not null check (document_type in ('DRIVER_LICENSE','VEHICLE_REGISTRATION','ROADWORTHINESS','INSURANCE')),
  object_path text not null unique,
  file_name text not null,
  mime_type text not null check (mime_type in ('application/pdf','image/jpeg','image/png')),
  file_size_bytes integer not null check (file_size_bytes > 0 and file_size_bytes <= 5242880),
  expires_at date,
  status text not null default 'PENDING' check (status in ('PENDING','APPROVED','REJECTED')),
  review_note text,
  reviewed_by text,
  reviewed_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists oka_rides_driver_documents_driver_created_idx
  on public.oka_rides_driver_documents (driver_id, created_at desc);

alter table public.oka_rides_driver_documents enable row level security;
revoke all on table public.oka_rides_driver_documents from anon, authenticated;
grant all on table public.oka_rides_driver_documents to service_role;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('oka-rides-driver-documents', 'oka-rides-driver-documents', false, 5242880,
        array['application/pdf','image/jpeg','image/png'])
on conflict (id) do update
set public = false, file_size_limit = 5242880,
    allowed_mime_types = excluded.allowed_mime_types;

