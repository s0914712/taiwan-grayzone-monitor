import json
from datetime import datetime

from src.generate_ccg_timeline_video import (
    MAP_BOUNDS,
    camera_bounds_for_frame,
    choose_frame_indices,
    choose_story_frame_indices,
    is_coast_guard,
    load_frames,
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
