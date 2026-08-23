import json
from datetime import datetime, timedelta, timezone

from src.generate_ccg_timeline_video import (
    MAP_BOUNDS,
    build_story_sequence,
    calendar_intro_labels,
    camera_bounds_for_frame,
    choose_frame_indices,
    choose_story_frame_indices,
    detect_story_events,
    fading_trail_segments,
    is_coast_guard,
    load_frames,
    near_taiwan,
    nearest_story_zone,
    parse_timestamp,
)


def test_parse_timestamp_normalizes_to_taiwan_time():
    dt = parse_timestamp("2026-08-23T07:00:00+00:00")
    assert dt is not None
    assert dt.hour == 15
    assert dt.utcoffset().total_seconds() == 8 * 3600


def test_coast_guard_filter_excludes_other_gov_vessels():
    assert is_coast_guard({"gov": "coastguard", "name": "UNKNOWN"})
    assert is_coast_guard({"name": "CHINA COAST GUARD 2501"})
    assert is_coast_guard({"name": "CCG 5901"})
    assert not is_coast_guard({"gov": "msa", "name": "HAIXUN 06"})
    assert not is_coast_guard({"gov": "rescue", "name": "DONG HAI JIU 101"})


def test_choose_frame_indices_has_exact_video_length():
    indices = choose_frame_indices(frame_count=10, duration=2, fps=5)
    assert len(indices) == 10
    assert indices[0] == 0
    assert indices[-1] == 9


def test_story_indices_keep_exact_length_and_endpoints():
    frames = [
        {"timestamp": datetime(2026, 8, 20), "vessels": []},
        {
            "timestamp": datetime(2026, 8, 21),
            "vessels": [{"mmsi": "1", "lat": 24.45, "lon": 118.35, "name": "CCG 1"}],
        },
        {"timestamp": datetime(2026, 8, 22), "vessels": []},
    ]
    indices = choose_story_frame_indices(frames, duration=2, fps=5)
    assert len(indices) == 10
    assert indices[0] == 0
    assert indices[-1] == 2
    assert indices.count(1) >= indices.count(0)


def test_calendar_intro_flips_from_january_to_august():
    labels = calendar_intro_labels(2026, 8, 8)
    assert labels == [
        "2026 JAN", "2026 FEB", "2026 MAR", "2026 APR",
        "2026 MAY", "2026 JUN", "2026 JUL", "2026 AUG",
    ]


def test_detect_story_events_for_kinmen_taiwan_and_multi_vessel_activity():
    tz = timezone(timedelta(hours=8))
    frames = [
        {"timestamp": datetime(2026, 8, 20, 8, tzinfo=tz), "vessels": []},
        {
            "timestamp": datetime(2026, 8, 21, 8, tzinfo=tz),
            "vessels": [{"mmsi": "1", "lat": 24.45, "lon": 118.35, "name": "CCG 1"}],
        },
        {
            "timestamp": datetime(2026, 8, 22, 8, tzinfo=tz),
            "vessels": [
                {"mmsi": "1", "lat": 24.2, "lon": 120.4, "name": "CCG 1"},
                {"mmsi": "2", "lat": 24.4, "lon": 120.7, "name": "CCG 2"},
                {"mmsi": "3", "lat": 24.6, "lon": 121.0, "name": "CCG 3"},
            ],
        },
    ]
    events = detect_story_events(frames)
    assert "ENTERING KINMEN WATERS" in events[1]["label"]
    assert "APPROACHING TAIWAN" in events[2]["label"]
    assert "3 CCG VESSELS ACTIVE" in events[2]["label"]
    assert 1.0 <= events[1]["hold_seconds"] <= 2.0
    assert 1.0 <= events[2]["hold_seconds"] <= 2.0


def test_story_sequence_keeps_total_duration_and_repeats_event_frames():
    tz = timezone(timedelta(hours=8))
    frames = [
        {"timestamp": datetime(2026, 8, 20, 8, tzinfo=tz), "vessels": []},
        {
            "timestamp": datetime(2026, 8, 21, 8, tzinfo=tz),
            "vessels": [{"mmsi": "1", "lat": 24.45, "lon": 118.35, "name": "CCG 1"}],
        },
        {"timestamp": datetime(2026, 8, 22, 8, tzinfo=tz), "vessels": []},
    ]
    sequence, events, intro = build_story_sequence(frames, duration=10, fps=10)
    assert len(sequence) + intro == 100
    assert 1 in events
    assert sequence.count(1) >= 10


def test_fading_trail_segments_get_brighter_toward_current_time():
    tz = timezone.utc
    now = datetime(2026, 8, 23, 12, tzinfo=tz)
    points = [
        (now - timedelta(hours=48), 24.0, 120.0),
        (now - timedelta(hours=24), 24.1, 120.1),
        (now - timedelta(hours=2), 24.2, 120.2),
        (now, 24.3, 120.3),
    ]
    segments = fading_trail_segments(points, now, trail_hours=72)
    assert len(segments) == 3
    alphas = [segment[2] for segment in segments]
    widths = [segment[3] for segment in segments]
    assert alphas == sorted(alphas)
    assert widths == sorted(widths)
    assert alphas[-1] > alphas[0]


def test_nearest_story_zone_and_camera_closeup():
    frame = {
        "timestamp": datetime(2026, 8, 23),
        "vessels": [{"mmsi": "1", "lat": 24.45, "lon": 118.35, "name": "CCG 1"}],
    }
    zone, distance = nearest_story_zone(frame)
    assert zone == "KINMEN"
    assert distance == 0
    bounds = camera_bounds_for_frame(frame)
    assert bounds != MAP_BOUNDS
    assert bounds[0] <= 24.45 <= bounds[1]
    assert bounds[2] <= 118.35 <= bounds[3]


def test_near_taiwan_single_vessel_also_triggers_closeup():
    frame = {
        "timestamp": datetime(2026, 8, 23),
        "vessels": [{"mmsi": "1", "lat": 24.1, "lon": 121.1, "name": "CCG 1"}],
    }
    assert near_taiwan(frame)
    assert camera_bounds_for_frame(frame) != MAP_BOUNDS


def test_story_camera_contains_all_separated_active_vessels():
    frame = {
        "timestamp": datetime(2026, 8, 23),
        "vessels": [
            {"mmsi": "1", "lat": 23.3, "lon": 118.5, "name": "CCG 1"},
            {"mmsi": "2", "lat": 26.8, "lon": 122.9, "name": "CCG 2"},
            {"mmsi": "3", "lat": 24.2, "lon": 120.0, "name": "CCG 3"},
        ],
    }
    bounds = camera_bounds_for_frame(frame)
    for vessel in frame["vessels"]:
        assert bounds[0] <= vessel["lat"] <= bounds[1]
        assert bounds[2] <= vessel["lon"] <= bounds[3]


def test_camera_stays_overview_for_empty_frame():
    frame = {"timestamp": datetime(2026, 8, 23), "vessels": []}
    assert camera_bounds_for_frame(frame) == MAP_BOUNDS


def test_load_frames_keeps_only_ccg_and_recent_days(tmp_path):
    data = [
        {
            "timestamp": "2026-08-01T00:00:00+00:00",
            "vessels": [
                {"mmsi": "1", "gov": "coastguard", "name": "CCG OLD", "lat": 24.0, "lon": 120.0}
            ],
        },
        {
            "timestamp": "2026-08-22T00:00:00+00:00",
            "vessels": [
                {"mmsi": "2", "gov": "coastguard", "name": "CCG 2501", "lat": 24.2, "lon": 120.2},
                {"mmsi": "3", "gov": "msa", "name": "HAIXUN 06", "lat": 24.3, "lon": 120.3},
                {"mmsi": "4", "gov": "coastguard", "name": "CCG OUTSIDE", "lat": 32.0, "lon": 120.0},
            ],
        },
        {
            "timestamp": "2026-08-23T00:00:00+00:00",
            "vessels": [
                {"mmsi": "2", "gov": "coastguard", "name": "CCG 2501", "lat": 24.4, "lon": 120.4}
            ],
        },
    ]
    p = tmp_path / "track.json"
    p.write_text(json.dumps(data), encoding="utf-8")

    frames = load_frames(p, days=2)
    assert len(frames) == 2
    assert [v["mmsi"] for v in frames[0]["vessels"]] == ["2"]
    assert [v["mmsi"] for v in frames[1]["vessels"]] == ["2"]
