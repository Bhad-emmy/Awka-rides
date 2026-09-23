create table if not exists public.oka_rides_drivers (
  id uuid primary key default gen_random_uuid(),
  driver_id text not null unique,
  name text not null,
  phone text not null,
  vehicle text not null,
  plate_number text not null,
  status text not null default 'ACTIVE' check (status in ('ACTIVE','INACTIVE','SUSPENDED')),
  verification_status text not null default 'PENDING' check (verification_status in ('PENDING','VERIFIED','REJECTED')),
  completed_rides integer not null default 0 check (completed_rides >= 0),
  cancelled_rides integer not null default 0 check (cancelled_rides >= 0),
  notes text,
  created_at timestamptz not null default now()
);

create table if not exists public.oka_rides_rides (
  id uuid primary key default gen_random_uuid(),
  reference text not null unique,
  name text not null,
  phone text not null,
  email text not null,
  pickup text not null,
  destination text not null,
  pickup_timing text not null,
  scheduled_time text,
  passengers text not null,
  notes text,
  vehicle text not null,
  status text not null default 'REQUESTED' check (status in ('REQUESTED','DRIVER ASSIGNED','PAID','PICKED UP','COMPLETED','CANCELLED')),
  created_at timestamptz not null default now(),
  driver_id text,
  driver_name text,
  driver_phone text,
  driver_vehicle text,
  driver_plate_number text,
  fare numeric(12,2),
  fare_confirmed boolean not null default false,
  payment_status text not null default 'PENDING' check (payment_status in ('PENDING','PAID','FAILED')),
  payment_reference text,
  payment_method text check (payment_method is null or payment_method in ('OPAY','PAYSTACK')),
  pickup_latitude double precision check (pickup_latitude is null or pickup_latitude between -90 and 90),
  pickup_longitude double precision check (pickup_longitude is null or pickup_longitude between -180 and 180),
  destination_latitude double precision check (destination_latitude is null or destination_latitude between -90 and 90),
  destination_longitude double precision check (destination_longitude is null or destination_longitude between -180 and 180),
  constraint oka_rides_rides_driver_fk foreign key (driver_id) references public.oka_rides_drivers(driver_id)
);

create index if not exists idx_oka_rides_rides_status on public.oka_rides_rides(status);
create index if not exists idx_oka_rides_rides_created_at on public.oka_rides_rides(created_at desc);
create index if not exists idx_oka_rides_rides_driver_id on public.oka_rides_rides(driver_id);

alter table public.oka_rides_drivers enable row level security;
alter table public.oka_rides_rides enable row level security;

insert into public.oka_rides_drivers
  (driver_id,name,phone,vehicle,plate_number,status,verification_status,completed_rides,cancelled_rides,notes)
values
  ('TEST-KEKE-001','Edwin Chukwuemeka','09064636239','Keke','TEST-001','ACTIVE','VERIFIED',0,0,'Test driver record for system testing only.')
on conflict (driver_id) do update set
  name=excluded.name,
  phone=excluded.phone,
  vehicle=excluded.vehicle,
  plate_number=excluded.plate_number,
  status=excluded.status,
  verification_status=excluded.verification_status,
  completed_rides=excluded.completed_rides,
  cancelled_rides=excluded.cancelled_rides,
  notes=excluded.notes;
