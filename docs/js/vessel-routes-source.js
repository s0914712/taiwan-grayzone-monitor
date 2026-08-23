/**
 * Taiwan Gray Zone Monitor - Vessel route data source
 *
 * Per-vessel routes (~30k vessels) are too many to ship inside the Pages
 * artifact, so they live in Supabase (`vessel_routes`, one row per MMSI,
 * anon read-only via RLS). Fetching one MMSI costs ~10-50KB instead of the
 * 31MB whole-fleet track file.
 *
 * Fallback order: Supabase → the legacy single-commit `vessel-data` git
 * branch via raw.githubusercontent → (caller's own) extraction from
 * ais_track_history.json. Any layer may be missing; each just falls through.
 *
 * Load this BEFORE js/map-routes.js.
 */
var VesselRouteSource = (function() {
    'use strict';

    // Publishable (anon) key: read-only by RLS policy, safe to ship in a
    // static page — same class of secret as a Google Maps browser key.
    var SUPABASE_URL = 'https://fyvaqwqnwgfutwfaaeei.supabase.co';
    var SUPABASE_ANON_KEY = 'sb_publishable_Zw9-XxcrUs6cXMz2Or0XDg_hytOzbg2';

    // Legacy fallback. On GitHub Pages the branch is only reachable via raw.
    var LEGACY_BASE = /\.github\.io$/.test(location.hostname)
        ? 'https://raw.githubusercontent.com/s0914712/taiwan-grayzone-monitor/vessel-data/data/vessel_routes/'
        : '../data/vessel_routes/';

    function _valid(data) {
        return data && data.track && data.track.length > 0 ? data : null;
    }

    function fromSupabase(mmsi) {
        if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return Promise.resolve(null);
        var url = SUPABASE_URL + '/rest/v1/vessel_routes?select=*&limit=1&mmsi=eq.' +
            encodeURIComponent(mmsi);
        return fetch(url, {
            headers: {
                apikey: SUPABASE_ANON_KEY,
                Authorization: 'Bearer ' + SUPABASE_ANON_KEY,
                Accept: 'application/json'
            }
        }).then(function(res) {
            if (!res.ok) return null;
            return res.json();
        }).then(function(rows) {
            return Array.isArray(rows) && rows.length ? _valid(rows[0]) : null;
        }).catch(function() { return null; });
    }

    function fromLegacyBranch(mmsi) {
        return fetch(LEGACY_BASE + mmsi + '.json?' + Date.now())
            .then(function(res) { return res.ok ? res.json() : null; })
            .then(_valid)
            .catch(function() { return null; });
    }

    /**
     * Load one vessel's route. Resolves with the route object or null.
     * Never rejects — callers fall back to track-history extraction.
     */
    function load(mmsi) {
        return fromSupabase(mmsi).then(function(data) {
            return data || fromLegacyBranch(mmsi);
        });
    }

    return {
        load: load,
        fromSupabase: fromSupabase,
        fromLegacyBranch: fromLegacyBranch,
        SUPABASE_URL: SUPABASE_URL,
        LEGACY_BASE: LEGACY_BASE
    };
})();
