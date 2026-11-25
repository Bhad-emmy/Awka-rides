-- Oka Rides only: private driver document storage and dispatcher review.
-- This migration intentionally puts every existing non-suspended driver back
-- into a pending/offline state until their documents have been reviewed.

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

update public.oka_rides_drivers
set status = case when status = 'SUSPENDED' then 'SUSPENDED' else 'INACTIVE' end,
    verification_status = 'PENDING',
    availability_status = 'OFFLINE'
where status <> 'SUSPENDED'
   or verification_status <> 'PENDING'
   or availability_status <> 'OFFLINE';
