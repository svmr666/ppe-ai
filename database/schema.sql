-- ============================================================
-- Industrial AI PPE & Safety Monitoring — database schema
-- Supabase SQL Editor дээр бүтнээр нь нэг удаа ажиллуулна.
-- Дахин ажиллуулахад аюулгүй (idempotent).
-- ============================================================

create extension if not exists "pgcrypto";

-- ---------- ENUM types ----------
do $$ begin
    create type zone_type as enum ('FACTORY', 'OFFICE');
exception when duplicate_object then null; end $$;

do $$ begin
    create type camera_type as enum ('DAHUA', 'HIKVISION', 'GENERIC_RTSP');
exception when duplicate_object then null; end $$;

-- LOCAL_WEBCAM: Mac/ноутбукийн суурилуулсан камер (ip_address талбарт төхөөрөмжийн дугаар "0", "1" ...)
alter type camera_type add value if not exists 'LOCAL_WEBCAM';

do $$ begin
    create type camera_status as enum
        ('OFFLINE', 'CONNECTING', 'ONLINE', 'STREAMING', 'ERROR', 'RECONNECTING');
exception when duplicate_object then null; end $$;

do $$ begin
    create type violation_type as enum
        ('NO_HELMET', 'NO_WORK_UNIFORM', 'OTHER_SAFETY_VIOLATION');
exception when duplicate_object then null; end $$;

do $$ begin
    create type violation_status as enum ('OPEN', 'ACKNOWLEDGED', 'RESOLVED');
exception when duplicate_object then null; end $$;

do $$ begin
    create type user_role as enum ('ADMIN', 'HUB_EMPLOYEE', 'VIEWER');
exception when duplicate_object then null; end $$;

-- ---------- zones ----------
create table if not exists zones (
    id           uuid primary key default gen_random_uuid(),
    name         text not null unique,
    type         zone_type not null,
    description  text,
    created_at   timestamptz not null default now()
);

-- ---------- cameras ----------
-- password_encrypted: backend Fernet (CAMERA_SECRET_KEY)-ээр шифрлэсэн утга.
-- API response-д ХЭЗЭЭ Ч буцаахгүй.
create table if not exists cameras (
    id                 uuid primary key default gen_random_uuid(),
    name               text not null unique,
    ip_address         text not null,
    rtsp_port          integer not null default 554 check (rtsp_port between 1 and 65535),
    username           text not null,
    password_encrypted text,
    camera_type        camera_type not null default 'DAHUA',
    channel            integer not null default 1 check (channel >= 1),
    subtype            integer not null default 0 check (subtype >= 0),
    zone_id            uuid references zones(id) on update cascade on delete set null,
    status             camera_status not null default 'OFFLINE',
    mirror             boolean not null default false,   -- вэбкамерын толин тусгал (selfie) харагдац
    ai_enabled         boolean not null default true,    -- AI-тай холбох: false бол зөвхөн шууд видео (хүн/каск/хувцас илрүүлэхгүй, зөрчил бүртгэхгүй)
    enabled            boolean not null default true,   -- Connect / Disconnect: AI service зөвхөн enabled камеруудыг ажиллуулна
    last_connected_at  timestamptz,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

-- ---------- violations ----------
-- Snapshot зургийн binary энд хадгалагдахгүй. Зөвхөн Supabase Storage-ийн URL/path.
create table if not exists violations (
    id              uuid primary key default gen_random_uuid(),
    camera_id       uuid not null references cameras(id) on update cascade on delete cascade,
    zone_id         uuid references zones(id) on update cascade on delete set null,
    violation_type  violation_type not null,
    person_id       text,                       -- tracker-ийн түр ID (camera-д хамаарах)
    confidence      real check (confidence >= 0 and confidence <= 1),
    snapshot_url    text,
    storage_path    text,
    detected_at     timestamptz not null default now(),
    status          violation_status not null default 'OPEN',
    resolved_at     timestamptz,
    resolved_by     text,
    created_at      timestamptz not null default now()
);

-- Өмнөх хувилбараас шинэчлэх үед (хүснэгт аль хэдийн байвал)
alter table cameras add column if not exists enabled boolean not null default true;
alter table cameras add column if not exists mirror boolean not null default false;
alter table cameras add column if not exists ai_enabled boolean not null default true;

-- ---------- users ----------
-- password_hash: bcrypt. Нууц үгийг plaintext-ээр хэзээ ч хадгалахгүй.
create table if not exists users (
    id             uuid primary key default gen_random_uuid(),
    email          text not null unique,
    full_name      text,
    password_hash  text not null,
    role           user_role not null default 'HUB_EMPLOYEE',   -- (enum-д VIEWER утга үлдсэн ч ашиглагдахгүй)
    is_active      boolean not null default true,
    created_at     timestamptz not null default now(),
    last_login_at  timestamptz
);

-- «Үзэгч» (VIEWER) эрх устсан: хуучин бүртгэлүүдийг HUB_EMPLOYEE болгоно
update users set role = 'HUB_EMPLOYEE' where role::text = 'VIEWER';

-- ---------- indexes ----------
create index if not exists idx_violations_detected_at on violations (detected_at desc);
create index if not exists idx_violations_status      on violations (status);
create index if not exists idx_violations_camera_id   on violations (camera_id);
create index if not exists idx_violations_zone_id     on violations (zone_id);
create index if not exists idx_cameras_status         on cameras (status);

-- ---------- updated_at trigger ----------
create or replace function set_updated_at() returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_cameras_updated_at on cameras;
create trigger trg_cameras_updated_at
    before update on cameras
    for each row execute function set_updated_at();

-- ---------- default zones ----------
insert into zones (name, type, description) values
    ('FACTORY', 'FACTORY', 'Үйлдвэрийн талбай: дуулга + ажлын хувцас ЗААВАЛ'),
    ('OFFICE',  'OFFICE',  'Оффис: ажлын хувцас заавал, дуулга заавал биш')
on conflict (name) do nothing;

-- ---------- Тохиргоо (key/value) ----------
-- dataset_collection: AI service-ийн сургалтын өгөгдөл цуглуулалт (backend бичиж, ai-service уншина)
create table if not exists app_settings (
    key         text primary key,
    value       jsonb not null default '{}'::jsonb,
    updated_at  timestamptz not null default now()
);

-- ---------- Row Level Security ----------
-- Бүх хандалт backend / ai-service-ээр (service role key) явна.
-- RLS асаалттай, policy байхгүй => anon/public түлхүүрээр шууд хандах боломжгүй.
alter table zones      enable row level security;
alter table cameras    enable row level security;
alter table violations enable row level security;
alter table users      enable row level security;
alter table app_settings enable row level security;

-- ---------- Storage bucket ----------
-- PRIVATE bucket: snapshot-д ажилчдын царай орсон тул URL-ээр шууд харагдахгүй.
-- Backend богино хугацаатай (signed) URL үүсгэж зөвхөн нэвтэрсэн хэрэглэгчид өгнө.
insert into storage.buckets (id, name, public)
values ('violation-snapshots', 'violation-snapshots', false)
on conflict (id) do update set public = false;
