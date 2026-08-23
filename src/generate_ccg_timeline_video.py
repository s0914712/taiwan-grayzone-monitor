#!/usr/bin/env python3
"""Render a 9:16 China Coast Guard AIS timeline story from Tier-1 history.

Visual language: rapid year/month calendar recap, fading vessel trails, pulsing
current positions, a date scrubber, automatic story holds, and smooth overview /
close-up camera moves. Public AIS only; this is not a real-time tactical picture.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
DEFAULT_INPUT = DOCS_DIR / "ais_track_history.json"
DEFAULT_OUTPUT = BASE_DIR / "artifacts" / "ccg-timeline.mp4"
TW_TZ = timezone(timedelta(hours=8))
MAP_BOUNDS = (20.7, 27.1, 117.0, 123.8)

# Broad storytelling anchors, deliberately not operational geofences.
FOCUS_ZONES = [
    ("KINMEN", 24.45, 118.35, 0.80),
    ("MATSU", 26.15, 119.95, 0.85),
    ("PENGHU", 23.55, 119.62, 0.95),
    ("TAIWAN STRAIT", 24.05, 119.55, 1.25),
    ("SW TAIWAN", 22.65, 120.20, 0.95),
]
TAIWAN_NEAR_BOUNDS = (21.55, 25.75, 119.85, 122.65)

BG = "#07111f"
GRID = "#203652"
TEXT = "#f7f9fd"
MUTED = "#8fa6c4"
ACCENT = "#4da3ff"
ACCENT_SOFT = "#91c7ff"
EVENT = "#ffd166"
CCG = "#ffffff"
TRAIL = "#72b8ff"

_COAST_GUARD_NAME = re.compile(
    r"(?:CHINA\s*COAST\s*GUARD|CHINACOASTGUARD|\bCCG\b|中國海警|海警)", re.I
)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TW_TZ)


def is_coast_guard(vessel: dict[str, Any]) -> bool:
    if vessel.get("gov") == "coastguard" or vessel.get("type_name") == "coastguard":
        return True
    return bool(_COAST_GUARD_NAME.search(str(vessel.get("name") or "")))


def short_name(vessel: dict[str, Any]) -> str:
    name = re.sub(r"\s+", " ", str(vessel.get("name") or "").strip())
    name = re.sub(r"CHINA\s*COAST\s*GUARD", "CCG", name, flags=re.I)
    if name:
        return name[:18]
    mmsi = str(vessel.get("mmsi") or "")
    return f"CCG {mmsi[-5:]}" if mmsi else "CCG"


def load_frames(path: Path, days: int = 14) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("AIS track history must be a list of snapshot entries")

    parsed: list[tuple[datetime, list[dict[str, Any]]]] = []
    for entry in raw:
        dt = parse_timestamp(entry.get("timestamp"))
        if dt is None:
            continue
        vessels = []
        for vessel in entry.get("vessels", []) or []:
            if not is_coast_guard(vessel):
                continue
            lat, lon = vessel.get("lat"), vessel.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            if MAP_BOUNDS[0] <= lat <= MAP_BOUNDS[1] and MAP_BOUNDS[2] <= lon <= MAP_BOUNDS[3]:
                vessels.append(vessel)
        parsed.append((dt, vessels))

    if not parsed:
        return []
    parsed.sort(key=lambda item: item[0])
    cutoff = parsed[-1][0] - timedelta(days=max(days, 1))
    return [{"timestamp": dt, "vessels": vessels} for dt, vessels in parsed if dt >= cutoff]


def choose_frame_indices(frame_count: int, duration: float, fps: int) -> list[int]:
    if frame_count <= 0:
        return []
    target = max(2, int(round(max(duration, 1.0) * max(fps, 1))))
    if frame_count == 1:
        return [0] * target
    return [round(i * (frame_count - 1) / (target - 1)) for i in range(target)]


def _unique_vessel_count(frame: dict[str, Any]) -> int:
    return len({str(v.get("mmsi") or short_name(v)) for v in frame.get("vessels", []) or []})


def nearest_story_zone(frame: dict[str, Any]) -> tuple[str | None, float | None]:
    best: tuple[str | None, float | None] = (None, None)
    for vessel in frame.get("vessels", []) or []:
        lat, lon = float(vessel["lat"]), float(vessel["lon"])
        for name, zlat, zlon, radius in FOCUS_ZONES:
            dx = (lon - zlon) * math.cos(math.radians((lat + zlat) / 2))
            dist = math.hypot(dx, lat - zlat)
            if dist <= radius and (best[1] is None or dist < best[1]):
                best = (name, dist)
    return best


def near_taiwan(frame: dict[str, Any]) -> bool:
    lat_min, lat_max, lon_min, lon_max = TAIWAN_NEAR_BOUNDS
    return any(
        lat_min <= float(v["lat"]) <= lat_max and lon_min <= float(v["lon"]) <= lon_max
        for v in frame.get("vessels", []) or []
    )


def frame_interest_score(frame: dict[str, Any]) -> float:
    score = min(3.2, 0.62 * _unique_vessel_count(frame))
    zone = nearest_story_zone(frame)[0]
    if zone:
        score += 1.8
    if near_taiwan(frame):
        score += 1.1
    if _unique_vessel_count(frame) >= 3:
        score += 1.4
    return score


def _choose_weighted_indices(frames: list[dict[str, Any]], target: int) -> list[int]:
    if not frames or target <= 0:
        return []
    if len(frames) == 1:
        return [0] * target
    weights = [1.0 + min(2.8, frame_interest_score(f) * 0.35) for f in frames]
    cumulative, total = [], 0.0
    for weight in weights:
        total += weight
        cumulative.append(total)
    result = [
        min(bisect_left(cumulative, (i / max(1, target - 1)) * total), len(frames) - 1)
        for i in range(target)
    ]
    result[0], result[-1] = 0, len(frames) - 1
    return result


def choose_story_frame_indices(frames: list[dict[str, Any]], duration: float, fps: int) -> list[int]:
    target = max(2, int(round(max(duration, 1.0) * max(fps, 1))))
    return _choose_weighted_indices(frames, target)


def detect_story_events(frames: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Identify narrative moments worth a 1-2 second social-video hold.

    Events are transition-based and use a cooldown to avoid repeatedly pausing on
    noisy/sparse AIS snapshots. They are storytelling cues, not operational alerts.
    """
    events: dict[int, dict[str, Any]] = {}
    cooldown = timedelta(hours=12)
    last_seen: dict[str, datetime] = {}

    for idx, frame in enumerate(frames):
        prev = frames[idx - 1] if idx > 0 else {"vessels": []}
        now = frame["timestamp"]
        triggers: list[tuple[str, str, float]] = []

        zone = nearest_story_zone(frame)[0]
        prev_zone = nearest_story_zone(prev)[0]
        if zone in {"KINMEN", "MATSU"} and zone != prev_zone:
            triggers.append((f"zone:{zone}", f"ENTERING {zone} WATERS", 1.8))

        if near_taiwan(frame) and not near_taiwan(prev):
            triggers.append(("near_taiwan", "APPROACHING TAIWAN", 1.4))

        count = _unique_vessel_count(frame)
        prev_count = _unique_vessel_count(prev)
        if count >= 3 and prev_count < 3:
            triggers.append(("multi", f"{count} CCG VESSELS ACTIVE", 1.6))

        accepted: list[tuple[str, float]] = []
        for key, label, hold in triggers:
            last = last_seen.get(key)
            if last is None or now - last >= cooldown:
                accepted.append((label, hold))
                last_seen[key] = now

        if accepted:
            labels = [label for label, _ in accepted]
            hold_seconds = min(2.0, max(h for _, h in accepted) + 0.15 * (len(accepted) - 1))
            events[idx] = {"label": " + ".join(labels), "hold_seconds": hold_seconds}
    return events


def build_story_sequence(
    frames: list[dict[str, Any]], duration: float, fps: int, intro_seconds: float = 1.8
) -> tuple[list[int], dict[int, dict[str, Any]], int]:
    """Build an exact-length story sequence with explicit event holds.

    The intro is a calendar-only JAN→AUG transition. Remaining frames are sampled
    from actual AIS snapshots; repeated source indices create true 1-2 second
    pauses without inventing intermediate vessel positions.
    """
    if not frames:
        return [], {}, 0
    total = max(2, int(round(max(duration, 1.0) * max(fps, 1))))
    intro = min(max(0, total - 2), int(round(min(intro_seconds, duration * 0.18) * max(fps, 1))))
    content_target = max(2, total - intro)
    events = detect_story_events(frames)

    desired_extra: dict[int, int] = {
        idx: max(0, int(round(float(info["hold_seconds"]) * fps)) - 1)
        for idx, info in events.items()
    }
    max_extra = int(content_target * 0.42)
    extra_total = sum(desired_extra.values())
    if extra_total > max_extra and extra_total > 0:
        scale = max_extra / extra_total
        desired_extra = {idx: int(round(count * scale)) for idx, count in desired_extra.items()}

    extras = sum(desired_extra.values())
    base_target = max(2, content_target - extras)
    base = _choose_weighted_indices(frames, base_target)
    sequence = list(base)
    for idx, extra in desired_extra.items():
        sequence.extend([idx] * extra)
    sequence.sort()

    if len(sequence) < content_target:
        fill = _choose_weighted_indices(frames, content_target - len(sequence))
        sequence.extend(fill)
        sequence.sort()
    elif len(sequence) > content_target:
        keep = [round(i * (len(sequence) - 1) / (content_target - 1)) for i in range(content_target)]
        sequence = [sequence[i] for i in keep]
    sequence[0], sequence[-1] = 0, len(frames) - 1
    return sequence, events, intro


def calendar_intro_labels(year: int = 2026, end_month: int = 8, frame_count: int = 48) -> list[str]:
    if frame_count <= 0:
        return []
    end_month = max(1, min(12, end_month))
    months = [datetime(year, month, 1).strftime("%b").upper() for month in range(1, end_month + 1)]
    if frame_count == 1:
        return [f"{year} {months[-1]}"]
    return [
        f"{year} {months[min(end_month - 1, int(i * end_month / frame_count))]}"
        for i in range(frame_count)
    ]


def build_history(frames: Iterable[dict[str, Any]]) -> dict[str, list[tuple[datetime, float, float]]]:
    history: dict[str, list[tuple[datetime, float, float]]] = defaultdict(list)
    for frame in frames:
        for vessel in frame["vessels"]:
            key = str(vessel.get("mmsi") or short_name(vessel))
            history[key].append((frame["timestamp"], float(vessel["lat"]), float(vessel["lon"])))
    return history


def fading_trail_segments(
    points: list[tuple[datetime, float, float]], now: datetime, trail_hours: float
) -> list[tuple[list[float], list[float], float, float]]:
    """Return consecutive route segments with age-based alpha and line width."""
    if len(points) < 2:
        return []
    hours = max(1.0, float(trail_hours))
    cutoff = now - timedelta(hours=hours)
    recent = [p for p in points if cutoff <= p[0] <= now]
    segments = []
    for p1, p2 in zip(recent, recent[1:]):
        age_hours = max(0.0, (now - p2[0]).total_seconds() / 3600.0)
        freshness = max(0.0, min(1.0, 1.0 - age_hours / hours))
        alpha = 0.08 + 0.78 * (freshness ** 1.55)
        width = 1.1 + 1.55 * freshness
        segments.append(([p1[2], p2[2]], [p1[1], p2[1]], alpha, width))
    return segments


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def camera_bounds_for_frame(frame: dict[str, Any]) -> tuple[float, float, float, float]:
    """Return a narrative close-up while keeping every active CCG vessel visible."""
    vessels = frame.get("vessels", []) or []
    zone = nearest_story_zone(frame)[0]
    close = bool(zone or near_taiwan(frame) or _unique_vessel_count(frame) >= 3)
    if not vessels or not close:
        return MAP_BOUNDS

    lats = [float(v["lat"]) for v in vessels]
    lons = [float(v["lon"]) for v in vessels]
    lat_center = (min(lats) + max(lats)) / 2
    lon_center = (min(lons) + max(lons)) / 2

    overview_lat_span = MAP_BOUNDS[1] - MAP_BOUNDS[0]
    overview_lon_span = MAP_BOUNDS[3] - MAP_BOUNDS[2]
    if zone in {"KINMEN", "MATSU"}:
        min_lat_span, min_lon_span = 3.4, 3.7
    elif near_taiwan(frame):
        min_lat_span, min_lon_span = 3.8, 4.1
    else:
        min_lat_span, min_lon_span = 4.5, 4.8

    lat_span = min(overview_lat_span, max(min_lat_span, max(lats) - min(lats) + 0.95))
    lon_span = min(overview_lon_span, max(min_lon_span, max(lons) - min(lons) + 0.95))
    lat_center = _clamp(lat_center, MAP_BOUNDS[0] + lat_span / 2, MAP_BOUNDS[1] - lat_span / 2)
    lon_center = _clamp(lon_center, MAP_BOUNDS[2] + lon_span / 2, MAP_BOUNDS[3] - lon_span / 2)
    return (
        lat_center - lat_span / 2,
        lat_center + lat_span / 2,
        lon_center - lon_span / 2,
        lon_center + lon_span / 2,
    )


def smooth_camera_bounds(
    frames: list[dict[str, Any]], indices: list[int], easing: float = 0.13
) -> list[tuple[float, float, float, float]]:
    if not indices:
        return []
    current = tuple(float(v) for v in MAP_BOUNDS)
    alpha = _clamp(easing, 0.02, 1.0)
    result = []
    for idx in indices:
        target = camera_bounds_for_frame(frames[idx])
        current = tuple(c + (t - c) * alpha for c, t in zip(current, target))
        result.append(current)
    return result


def _configure_fonts(plt: Any) -> None:
    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK TC", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "DejaVu Sans"
    ]
    plt.rcParams["axes.unicode_minus"] = False


def _draw_static_map(ax: Any) -> None:
    from map_basemap import draw_land

    ax.set_facecolor(BG)
    draw_land(ax, MAP_BOUNDS, zorder=1, linewidth=0.65)
    ax.set_xlim(MAP_BOUNDS[2], MAP_BOUNDS[3])
    ax.set_ylim(MAP_BOUNDS[0], MAP_BOUNDS[1])
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([118, 120, 122])
    ax.set_yticks([22, 24, 26])
    ax.grid(True, color=GRID, alpha=0.42, linewidth=0.7)
    ax.tick_params(colors=MUTED, labelsize=10.5, length=0)
    for spine in ax.spines.values():
        spine.set_color(GRID)
        spine.set_linewidth(0.9)
    for lat, lon, label in [
        (23.72, 120.95, "TAIWAN"), (24.45, 118.35, "KINMEN"),
        (26.15, 119.95, "MATSU"), (23.55, 119.62, "PENGHU"),
        (24.10, 119.15, "TAIWAN STRAIT"),
    ]:
        ax.text(lon, lat, label, color=MUTED, fontsize=10.5, alpha=0.72,
                ha="center", va="center", zorder=2)


def render_video(
    frames: list[dict[str, Any]], output: Path, duration: float = 20.0,
    fps: int = 30, trail_hours: float = 96.0, dpi: int = 100,
    preview: Path | None = None,
) -> Path:
    if not frames:
        raise RuntimeError("No AIS snapshots available for the requested period")
    if not any(frame["vessels"] for frame in frames):
        raise RuntimeError("No China Coast Guard AIS positions found in the requested period")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter
    from matplotlib.patheffects import withStroke

    _configure_fonts(plt)
    output.parent.mkdir(parents=True, exist_ok=True)
    if preview:
        preview.parent.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(10.8, 19.2), dpi=dpi, facecolor=BG)
    ax = fig.add_axes([0.055, 0.185, 0.89, 0.60])
    _draw_static_map(ax)

    fig.text(0.07, 0.958, "TIMELINE", color=ACCENT, fontsize=12, fontweight="bold", va="top")
    month_text = fig.text(0.07, 0.927, "", color=TEXT, fontsize=34, fontweight="bold", va="top")
    day_text = fig.text(0.07, 0.885, "", color=TEXT, fontsize=52, fontweight="bold", va="top")
    year_text = fig.text(0.245, 0.894, "", color=MUTED, fontsize=16, fontweight="bold", va="top")
    time_text = fig.text(0.245, 0.868, "", color=MUTED, fontsize=14, va="top")
    fig.text(0.93, 0.958, "CHINA COAST GUARD", color=MUTED, fontsize=11,
             fontweight="bold", ha="right", va="top")
    count_text = fig.text(0.93, 0.914, "", color=TEXT, fontsize=38,
                          fontweight="bold", ha="right", va="top")
    fig.text(0.93, 0.879, "VESSELS OBSERVED", color=MUTED, fontsize=10.5,
             ha="right", va="top")

    prev_day = fig.text(0.12, 0.812, "", color=MUTED, fontsize=12, ha="center", va="center")
    current_day = fig.text(0.50, 0.812, "", color=TEXT, fontsize=15,
                           fontweight="bold", ha="center", va="center")
    next_day = fig.text(0.88, 0.812, "", color=MUTED, fontsize=12, ha="center", va="center")
    focus_text = fig.text(0.50, 0.792, "", color=ACCENT_SOFT, fontsize=10.5,
                          fontweight="bold", ha="center", va="center")
    event_text = fig.text(0.50, 0.770, "", color=EVENT, fontsize=11.5,
                          fontweight="bold", ha="center", va="center")

    line_bg = plt.Line2D([0.07, 0.93], [0.105, 0.105], transform=fig.transFigure,
                         color=GRID, linewidth=7, solid_capstyle="round")
    line_fg = plt.Line2D([0.07, 0.07], [0.105, 0.105], transform=fig.transFigure,
                         color=ACCENT, linewidth=7, solid_capstyle="round")
    thumb = plt.Line2D([0.07], [0.105], transform=fig.transFigure, color=CCG,
                       marker="o", markersize=9, markeredgecolor=ACCENT,
                       markeredgewidth=2, linestyle="None")
    fig.lines.extend([line_bg, line_fg, thumb])
    progress_text = fig.text(0.07, 0.078, "", color=MUTED, fontsize=11.5, va="top")
    fig.text(0.07, 0.047,
             "Public AIS observations. Signals may be absent, delayed, spoofed, or incomplete; not a real-time tactical picture.",
             color=MUTED, fontsize=9.8, va="top", wrap=True)
    fig.text(0.93, 0.022, "Taiwan Gray Zone Monitor", color=MUTED,
             fontsize=10.5, ha="right", va="bottom")

    indices, events, intro_frames = build_story_sequence(frames, duration, fps)
    intro_labels = calendar_intro_labels(2026, 8, intro_frames)
    cameras = smooth_camera_bounds(frames, indices)
    history = build_history(frames)
    source_start, source_end = frames[0]["timestamp"], frames[-1]["timestamp"]
    artists: list[Any] = []
    stroke = [withStroke(linewidth=3.6, foreground=BG)]
    total_video_frames = intro_frames + len(indices)

    def clear_dynamic() -> None:
        while artists:
            artist = artists.pop()
            try:
                artist.remove()
            except (ValueError, AttributeError):
                pass

    def set_progress(video_idx: int) -> None:
        progress = video_idx / max(1, total_video_frames - 1)
        x = 0.07 + 0.86 * progress
        line_fg.set_xdata([0.07, x])
        thumb.set_xdata([x])

    def draw_intro_frame(label: str, video_idx: int) -> None:
        clear_dynamic()
        ax.set_xlim(MAP_BOUNDS[2], MAP_BOUNDS[3])
        ax.set_ylim(MAP_BOUNDS[0], MAP_BOUNDS[1])
        year, month = label.split()
        month_text.set_text(year)
        day_text.set_text(month)
        year_text.set_text("RECAP")
        time_text.set_text("JAN → AUG · CALENDAR TRANSITION")
        count_text.set_text("—")
        prev_day.set_text("2026 JAN")
        current_day.set_text(label)
        next_day.set_text("2026 AUG")
        focus_text.set_text("AIS PLAYBACK STARTS WITH AVAILABLE HISTORY")
        event_text.set_text("")
        progress_text.set_text("2026 CALENDAR RECAP  →  CHINA COAST GUARD AIS ACTIVITY")
        set_progress(video_idx)

    def draw_story_frame(source_idx: int, content_idx: int, video_idx: int) -> None:
        clear_dynamic()
        frame, now = frames[source_idx], frames[source_idx]["timestamp"]
        vessels = frame["vessels"]
        lat_min, lat_max, lon_min, lon_max = cameras[content_idx]
        ax.set_xlim(lon_min, lon_max)
        ax.set_ylim(lat_min, lat_max)

        # Draw every vessel's recent observed route, not only vessels present in
        # the current snapshot. Each segment fades by age to mimic Timeline-style
        # motion trails without synthesising intermediate AIS fixes.
        for points in history.values():
            for xs, ys, alpha, width in fading_trail_segments(points, now, trail_hours):
                artists.append(ax.plot(xs, ys, color=TRAIL, linewidth=width, alpha=alpha,
                                       zorder=4, solid_capstyle="round")[0])

        visible_ids: set[str] = set()
        if vessels:
            lons = [float(v["lon"]) for v in vessels]
            lats = [float(v["lat"]) for v in vessels]
            visible_ids = {str(v.get("mmsi") or short_name(v)) for v in vessels}
            pulse = 0.5 + 0.5 * math.sin(video_idx * 2 * math.pi / max(8, fps))
            artists.append(ax.scatter(lons, lats, s=390 + 210 * pulse, color=ACCENT,
                                      alpha=0.065, edgecolors="none", zorder=5))
            artists.append(ax.scatter(lons, lats, s=185 + 100 * pulse, color=ACCENT,
                                      alpha=0.14, edgecolors="none", zorder=5))
            artists.append(ax.scatter(lons, lats, s=68, color=CCG, edgecolors=ACCENT,
                                      linewidths=1.7, zorder=6))
            for vessel in vessels[:5]:
                txt = ax.annotate(short_name(vessel),
                                  (float(vessel["lon"]), float(vessel["lat"])),
                                  xytext=(7, 7), textcoords="offset points",
                                  color=TEXT, fontsize=9.0, fontweight="bold", zorder=7)
                txt.set_path_effects(stroke)
                artists.append(txt)

        month_text.set_text(now.strftime("%b").upper())
        day_text.set_text(now.strftime("%d"))
        year_text.set_text(now.strftime("%Y"))
        time_text.set_text(now.strftime("%H:%M  UTC+8"))
        count_text.set_text(str(len(visible_ids)))
        prev_day.set_text((now - timedelta(days=1)).strftime("%b %d").upper())
        current_day.set_text(now.strftime("%A · %b %d").upper())
        next_day.set_text((now + timedelta(days=1)).strftime("%b %d").upper())
        zone = nearest_story_zone(frame)[0]
        focus_text.set_text(f"●  FOCUS · {zone}" if zone else "OVERVIEW · TAIWAN WATERS")
        event = events.get(source_idx)
        event_text.set_text(f"●  AUTO PAUSE · {event['label']}" if event else "")

        elapsed_days = max(0, (now.date() - source_start.date()).days)
        total_days = max(1, (source_end.date() - source_start.date()).days + 1)
        progress_text.set_text(
            f"DAY {min(elapsed_days + 1, total_days)} / {total_days}     {source_start:%Y-%m-%d}  →  {source_end:%Y-%m-%d}"
        )
        set_progress(video_idx)

    if preview:
        draw_story_frame(indices[-1], len(indices) - 1, total_video_frames - 1)
        fig.savefig(preview, dpi=dpi, facecolor=BG)

    writer = FFMpegWriter(
        fps=max(1, fps), codec="libx264", bitrate=6500,
        metadata={
            "title": "China Coast Guard AIS Timeline Around Taiwan",
            "artist": "Taiwan Gray Zone Monitor",
            "comment": "Public AIS observations; not a real-time tactical picture.",
        },
        extra_args=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
    )
    with writer.saving(fig, str(output), dpi=dpi):
        for intro_idx, label in enumerate(intro_labels):
            draw_intro_frame(label, intro_idx)
            writer.grab_frame(facecolor=BG)
        for content_idx, source_idx in enumerate(indices):
            video_idx = intro_frames + content_idx
            draw_story_frame(source_idx, content_idx, video_idx)
            writer.grab_frame(facecolor=BG)
    plt.close(fig)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a portrait CCG AIS timeline MP4")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--preview", type=Path, default=None)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--trail-hours", type=float, default=96.0)
    parser.add_argument("--dpi", type=int, default=100, help="100 dpi = 1080x1920")
    args = parser.parse_args()

    if not args.input.exists():
        parser.error(f"input file not found: {args.input}")
    frames = load_frames(args.input, days=args.days)
    ccg_positions = sum(len(f["vessels"]) for f in frames)
    unique = {str(v.get("mmsi") or short_name(v)) for f in frames for v in f["vessels"]}
    if not unique:
        raise SystemExit("No China Coast Guard AIS positions found; refusing to render an empty/misleading video")
    print(f"Loaded {len(frames)} snapshots, {ccg_positions} CCG positions, {len(unique)} unique vessels")
    out = render_video(frames, args.output, args.duration, args.fps,
                       args.trail_hours, args.dpi, args.preview)
    print(f"✅ CCG timeline video: {out}")
    if args.preview:
        print(f"✅ Preview image: {args.preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
