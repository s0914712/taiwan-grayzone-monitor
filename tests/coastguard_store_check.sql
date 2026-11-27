-- Synthetic data only. Run after sql/coastguard_private.sql in a disposable database.
begin;
truncate public.mst_ccg_runs, public.mst_ccg_positions, public.mst_ccg_latest, public.mst_ccg_state;
insert into public.mst_ccg_state(id) values(1);

do $$
declare
    reply jsonb;
    point jsonb := jsonb_build_object('source', 'myshiptracking_terrestrial',
        'mmsi', '413875010', 'received', now() - interval '5 minutes', 'lat', 24.5, 'lon', 122);
begin
    if has_table_privilege('anon', 'public.mst_ccg_positions', 'SELECT')
       or has_table_privilege('authenticated', 'public.mst_ccg_runs', 'SELECT')
       or has_function_privilege('anon', 'public.mst_ccg_claim(text,integer,integer)', 'EXECUTE')
       or has_function_privilege('authenticated', 'public.mst_ccg_finish(text,jsonb,integer)', 'EXECUTE') then
        raise exception 'Commercial data or RPC exposed to non-service roles';
    end if;
    if not has_function_privilege('service_role', 'public.mst_ccg_claim(text,integer,integer)', 'EXECUTE') then
        raise exception 'Service role cannot claim';
    end if;
    reply := public.mst_ccg_claim('one', 2, 10);
    if reply->>'claimed' <> 'true' then raise exception 'First claim rejected'; end if;
    reply := public.mst_ccg_claim('one', 2, 10);
    if reply->>'reason' <> 'duplicate_run' then raise exception 'Duplicate run accepted'; end if;
    reply := public.mst_ccg_claim('other', 2, 10);
    if reply->>'reason' <> 'not_due' then raise exception 'Six-hour lock bypassed'; end if;

    perform public.mst_ccg_finish('one', jsonb_build_object('status','success',
        'observations',jsonb_build_array(point)), 1);
    reply := public.mst_ccg_finish('one', '{"status":"error"}', 0);
    if reply->>'duplicate' <> 'true'
       or (select charged from public.mst_ccg_runs where run_id='one') <> 1 then
        raise exception 'Finish is not idempotent';
    end if;
    if (select last_fresh_at is null from public.mst_ccg_state where id=1) then
        raise exception 'Fresh observation not recorded';
    end if;

    update public.mst_ccg_state set last_attempt_at=now()-interval '7 hours' where id=1;
    reply := public.mst_ccg_claim('two', 2, 3);
    if reply->>'claimed' <> 'true' or reply->'latest'->'413875010' is null then
        raise exception 'Released credits or latest position missing';
    end if;
    perform public.mst_ccg_finish('two', jsonb_build_object('status','partial_success',
        'observations',jsonb_build_array(point || '{"lat":26}',
            point || jsonb_build_object('received',now()-interval '2 hours','lat',25))), null);
    if (select count(*) from public.mst_ccg_positions) <> 2 then
        raise exception 'Position deduplication failed';
    end if;
    if (select l.point->>'lat' from public.mst_ccg_latest l where mmsi='413875010') <> '24.5' then
        raise exception 'Old/conflicting observation replaced latest';
    end if;
    update public.mst_ccg_state set last_attempt_at=now()-interval '7 hours' where id=1;
    reply := public.mst_ccg_claim('three', 2, 4);
    if reply->>'reason' <> 'daily_limit' then
        raise exception 'Unknown request cost did not retain reservation';
    end if;

    insert into public.mst_ccg_positions values ('myshiptracking_terrestrial', '413875010',
        now()-interval '91 days', point);
    insert into public.mst_ccg_runs(run_id,created_at,reserved,charged)
        values('expired',now()-interval '91 days',1,0);
    reply := public.mst_ccg_claim('four', 1, 4);
    if reply->>'claimed' <> 'true' then raise exception 'Final claim rejected'; end if;
    perform public.mst_ccg_finish('four', '{"status":"partial_success","observations":[]}', 0);
    if exists(select 1 from public.mst_ccg_runs where run_id='expired')
       or exists(select 1 from public.mst_ccg_positions where received<now()-interval '90 days') then
        raise exception 'Private retention failed';
    end if;
end;
$$;

-- Verify granted access works under the actual restricted execution role.
do $$
begin
    delete from public.mst_ccg_state;
    begin
        perform public.mst_ccg_claim('missing-state', 1, 10);
        raise exception 'Missing state was accepted';
    exception when others then
        if sqlerrm <> 'Tracking state is not initialized' then raise; end if;
    end;
    insert into public.mst_ccg_state(id,last_attempt_at) values(1,now());
end;
$$;
set local role service_role;
select public.mst_ccg_claim('five', 1, 4)->>'reason' = 'not_due' as cooldown_enforced;
reset role;
rollback;
