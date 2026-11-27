-- Apply manually in Supabase SQL Editor. Never expose these tables through anon RLS.
begin;

create table if not exists public.mst_ccg_state (
    id integer primary key check (id = 1),
    created_at timestamptz not null default now(),
    last_attempt_at timestamptz,
    last_success_at timestamptz,
    last_fresh_at timestamptz
);
insert into public.mst_ccg_state(id) values (1) on conflict do nothing;

create table if not exists public.mst_ccg_runs (
    run_id text primary key,
    created_at timestamptz not null default now(),
    reserved integer not null check (reserved between 1 and 100),
    charged integer check (charged >= 0),
    finished_at timestamptz,
    result jsonb
);
create index if not exists mst_ccg_runs_created on public.mst_ccg_runs(created_at);

create table if not exists public.mst_ccg_positions (
    source text not null,
    mmsi text not null check (mmsi ~ '^[0-9]{9}$'),
    received timestamptz not null,
    point jsonb not null,
    primary key (source, mmsi, received)
);
create index if not exists mst_ccg_positions_time on public.mst_ccg_positions(received);

create table if not exists public.mst_ccg_latest (
    mmsi text primary key,
    received timestamptz not null,
    point jsonb not null
);

alter table public.mst_ccg_state enable row level security;
alter table public.mst_ccg_runs enable row level security;
alter table public.mst_ccg_positions enable row level security;
alter table public.mst_ccg_latest enable row level security;
revoke all on public.mst_ccg_state, public.mst_ccg_runs,
    public.mst_ccg_positions, public.mst_ccg_latest from public, anon, authenticated;
grant select on public.mst_ccg_state, public.mst_ccg_runs,
    public.mst_ccg_positions, public.mst_ccg_latest to service_role;

create or replace function public.mst_ccg_claim(
    p_run_id text, p_max_cost integer, p_daily_limit integer
) returns jsonb language plpgsql security definer
set search_path = pg_catalog, public as $$
declare
    s public.mst_ccg_state%rowtype;
    used integer;
    latest jsonb;
begin
    if p_run_id is null or length(p_run_id) not between 1 and 128
       or p_max_cost is null or p_max_cost not between 1 and 100
       or p_daily_limit is null or p_daily_limit < p_max_cost then
        raise exception 'Invalid tracking budget or run ID';
    end if;
    -- One locked singleton serializes claims across manual runs and all runners.
    select * into s from public.mst_ccg_state where id = 1 for update;
    if exists (select 1 from public.mst_ccg_runs where run_id = p_run_id) then
        return jsonb_build_object('claimed', false, 'reason', 'duplicate_run');
    end if;
    if s.last_attempt_at > now() - interval '6 hours' then
        return jsonb_build_object('claimed', false, 'reason', 'not_due');
    end if;
    -- An unfinished/ambiguous request retains its full reservation.
    select coalesce(sum(coalesce(charged, reserved)), 0) into used
        from public.mst_ccg_runs
        where created_at >= date_trunc('day', now() at time zone 'UTC') at time zone 'UTC';
    if used + p_max_cost > p_daily_limit then
        return jsonb_build_object('claimed', false, 'reason', 'daily_limit');
    end if;
    insert into public.mst_ccg_runs(run_id, reserved) values(p_run_id, p_max_cost);
    update public.mst_ccg_state set last_attempt_at = now() where id = 1;
    select coalesce(jsonb_object_agg(mmsi, point), '{}'::jsonb) into latest
        from public.mst_ccg_latest;
    return jsonb_build_object('claimed', true, 'latest', latest,
        'overdue', coalesce(s.last_fresh_at, s.created_at) < now() - interval '8 hours');
end;
$$;

create or replace function public.mst_ccg_finish(
    p_run_id text, p_result jsonb, p_charged integer
) returns jsonb language plpgsql security definer
set search_path = pg_catalog, public as $$
declare
    r public.mst_ccg_runs%rowtype;
    observation jsonb;
    saw_fresh boolean := false;
begin
    if p_result is null or jsonb_typeof(p_result) <> 'object'
       or (p_charged is not null and p_charged < 0) then
        raise exception 'Invalid tracking result';
    end if;
    select * into r from public.mst_ccg_runs where run_id = p_run_id for update;
    if not found then raise exception 'No tracking claim'; end if;
    if r.finished_at is not null then
        return jsonb_build_object('stored', true, 'duplicate', true);
    end if;
    for observation in select value from jsonb_array_elements(
        coalesce(p_result->'observations', '[]'::jsonb)) loop
        if observation->>'source' <> 'myshiptracking_terrestrial'
           or observation->>'mmsi' is null
           or observation->>'received' is null then
            raise exception 'Invalid observation';
        end if;
        insert into public.mst_ccg_positions(source, mmsi, received, point)
            values(observation->>'source', observation->>'mmsi',
                (observation->>'received')::timestamptz, observation)
            on conflict do nothing;
        -- Conflicting same-time coordinates stay in the run's raw result, never overwrite.
        insert into public.mst_ccg_latest(mmsi, received, point)
            values(observation->>'mmsi', (observation->>'received')::timestamptz, observation)
            on conflict (mmsi) do update set received = excluded.received, point = excluded.point
            where excluded.received > mst_ccg_latest.received;
        saw_fresh := saw_fresh or
            (observation->>'received')::timestamptz >= now() - interval '1 hour';
    end loop;
    update public.mst_ccg_runs set result = p_result, charged = p_charged,
        finished_at = now() where run_id = p_run_id;
    if p_result->>'status' in ('success', 'partial_success') then
        update public.mst_ccg_state set last_success_at = now(),
            last_fresh_at = case when saw_fresh then now() else last_fresh_at end where id = 1;
    end if;
    -- Retain 90 days of private history, but never prune the watchlist or last position.
    delete from public.mst_ccg_positions where received < now() - interval '90 days';
    delete from public.mst_ccg_runs where created_at < now() - interval '90 days';
    return jsonb_build_object('stored', true, 'duplicate', false);
end;
$$;

revoke all on function public.mst_ccg_claim(text, integer, integer) from public, anon, authenticated;
revoke all on function public.mst_ccg_finish(text, jsonb, integer) from public, anon, authenticated;
grant execute on function public.mst_ccg_claim(text, integer, integer) to service_role;
grant execute on function public.mst_ccg_finish(text, jsonb, integer) to service_role;
commit;
