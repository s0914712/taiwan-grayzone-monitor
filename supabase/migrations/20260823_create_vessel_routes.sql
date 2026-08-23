-- Taiwan Gray Zone Monitor: per-vessel AIS route storage.
-- Replaces the 30k-file `vessel-data` git branch (force-pushed every CI run).
-- One row per MMSI; the track itself stays JSON (jsonb -> TOAST compressed,
-- measured ~3.0x on real-shaped route data with Postgres 17 pglz).
--
-- Applied to project fyvaqwqnwgfutwfaaeei on 2026-08-23. Kept here so the
-- schema is reproducible from the repo alone.
create table if not exists public.vessel_routes (
    mmsi        text primary key,
    name        text        not null default '',
    imo         text        not null default '',
    flag        text        not null default '',
    type        text        not null default '',
    point_count integer     not null default 0,
    first_seen  timestamptz,
    last_seen   timestamptz,
    track       jsonb       not null default '[]'::jsonb,
    updated_at  timestamptz not null default now()
);

comment on table public.vessel_routes is
    'Taiwan Gray Zone Monitor - per-vessel AIS route (14d tier-1 + 28d tier-2 window). Written by src/extract_all_routes.py with the service_role key; read anonymously by the GitHub Pages frontend.';

-- Gov/research track plotting filters by type; dashboards sort by recency.
create index if not exists vessel_routes_type_idx      on public.vessel_routes (type);
create index if not exists vessel_routes_last_seen_idx on public.vessel_routes (last_seen desc);

-- Frontend reads with the publishable (anon) key, so reads must be open but
-- writes must not: the pipeline uses the service_role key, which bypasses RLS.
alter table public.vessel_routes enable row level security;

drop policy if exists "vessel_routes anon read" on public.vessel_routes;
create policy "vessel_routes anon read"
    on public.vessel_routes for select
    to anon, authenticated
    using (true);

grant select on public.vessel_routes to anon, authenticated;
