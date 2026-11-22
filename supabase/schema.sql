-- Run in the Supabase SQL Editor before deploying the service.
create table if not exists public.site_runs (
    region text not null,
    site_key text not null,
    version text not null,
    app text not null,
    scheduled_time timestamptz not null,
    checked_at timestamptz not null,
    state text not null,
    status_url text not null,
    latest_entry jsonb,
    alert_sent boolean not null default false,
    alert_episode_key text,
    error_message text,
    recovered_by_scheduled_time timestamptz,
    primary key (region, site_key, scheduled_time)
);

alter table public.site_runs add column if not exists version text not null default 'unknown';
alter table public.site_runs add column if not exists app text not null default 'unknown';
alter table public.site_runs add column if not exists alert_episode_key text;
alter table public.site_runs add column if not exists recovered_by_scheduled_time timestamptz;

create index if not exists site_runs_checked_at_idx
    on public.site_runs (checked_at desc);
create index if not exists site_runs_site_history_idx
    on public.site_runs (site_key, checked_at desc);

create table if not exists public.alert_deliveries (
    dedupe_key text primary key,
    kind text not null,
    region text not null,
    site_key text not null default '',
    scheduled_time timestamptz,
    subject text not null,
    message text not null,
    sent_at timestamptz not null
);

create table if not exists public.site_configs (
    region text not null,
    site_key text not null,
    cron text not null,
    max_duration_minutes integer not null default 120 check (max_duration_minutes > 0),
    version text not null check (version in ('v1', 'v2')),
    app text not null check (app in ('shopify', 'bigcommerce')),
    is_enabled boolean not null default true,
    created_at timestamptz not null default now(),
    primary key (region, site_key)
);

alter table public.site_configs add column if not exists is_enabled boolean not null default true;

create index if not exists alert_deliveries_run_idx
    on public.alert_deliveries (region, site_key, scheduled_time);
create index if not exists alert_deliveries_sent_at_idx
    on public.alert_deliveries (sent_at desc);

-- Browser access is only through the Flask service. The service-role key stays
-- server-side and bypasses RLS; anonymous/authenticated clients get no policies.
alter table public.site_runs enable row level security;
alter table public.alert_deliveries enable row level security;
alter table public.site_configs enable row level security;

-- Table grants are required by PostgREST even when the key bypasses RLS.
grant usage on schema public to service_role;
grant select, insert, update, delete on public.site_runs to service_role;
grant select, insert, update, delete on public.alert_deliveries to service_role;
grant select, insert, update, delete on public.site_configs to service_role;
