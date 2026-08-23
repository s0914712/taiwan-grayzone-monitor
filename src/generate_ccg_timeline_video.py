#!/usr/bin/env python3
"""Generate a vertical China Coast Guard AIS timeline video.

The renderer uses the repository's Tier-1 ``docs/ais_track_history.json`` snapshots
and intentionally keeps the map tactical-detail-light: it visualizes publicly
observed AIS positions over time, not a real-time operational picture.

Examples
--------
    python src/generate_ccg_timeline_video.py
    python src/generate_ccg_timeline_video.py --days 7 --duration 18 --fps 30 \
        --output artifacts/ccg-timeline.mp4
    python src/generate_ccg_timeline_video.py --preview artifacts/ccg-preview.png
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
DEFAULT_INPUT = DOCS_DIR / "ais_track_history.json"
DEFAULT_OUTPUT = BASE_DIR / "artifacts" / "ccg-timeline.mp4"
TW_TZ = timezone(timedelta(hours=8))

# Fixed portrait framing. It covers the Taiwan Strait, Taiwan proper, the northern
# Bashi Channel, Fujian coast, and the waters northeast of Taiwan without camera
# jumps that make time-lapse comparison difficult.
MAP_BOUNDS = (20.7, 27.1, 117.0, 123.8)  # lat_min, lat_max, lon_min, lon_max

BG = "#07111f"
PANEL = "#0d1a2d"
LAND = "#182740"
GRID = "#203652"
TEXT = "#f5f7fb"
MUTED = "#8fa6c4"
ACCENT = "#4da3ff"
CCG = "#ffffff"
TRAIL = "#8ec5ff"
ALERT = "#ff5c72"

_COAST_GUARD_NAME = re.compile(
    r"(?:CHINA\s*COAST\s*GUARD|CHINACOASTGUARD|\bCCG\b|中國海警|海警)",
    re.IGNORECASE,
)


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO timestamp and normalize it to Taiwan time."""
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
    """Return True only for China Coast Guard-classified vessels.

    New Tier-1 snapshots carry ``gov='coastguard'``. The name fallback preserves
    compatibility with older snapshots generated before the gov subtype field was
    added. MSA / rescue / research vessels are deliberately excluded.
    """
    if vessel.get("gov") == "coastguard" or vessel.get("type_name") == "coastguard":
        return True
    return bool(_COAST_GUARD_NAME.search(str(vessel.get("name") or "")))


def short_name(vessel: dict[str, Any]) -> str:
    name = re.sub(r"\s+", " ", str(vessel.get("name") or "").strip())
    name = re.sub(r"CHINA\s*COAST\s*GUARD", "CCG", name, flags=re.IGNORECASE)
    if name:
        return name[:18]
    mmsi = str(vessel.get("mmsi") or "")
    return f"CCG {mmsi[-5:]}" if mmsi else "CCG"


def load_frames(path: Path, days: int = 14) -> list[dict[str, Any]]:
    """Load and filter Tier-1 snapshots to CCG vessels inside the map window."""
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
            if not (MAP_BOUNDS[0] <= lat <= MAP_BOUNDS[1] and MAP_BOUNDS[2] <= lon <= MAP_BOUNDS[3]):
                continue
            vessels.append(vessel)
        parsed.append((dt, vessels))

    if not parsed:
        return []
    parsed.sort(key=lambda x: x[0])
    cutoff = parsed[-1][0] - timedelta(days=max(days, 1))
    return [
        {"timestamp": dt, "vessels": vessels}
        for dt, vessels in parsed
        if dt >= cutoff
    ]


def choose_frame_indices(frame_count: int, duration: float, fps: int) -> list[int]:
    """Evenly sample source snapshots to the requested video duration."""
    if frame_count <= 0:
        return []
    target = max(2, int(round(max(duration, 1.0) * max(fps, 1))))
    if frame_count == 1:
        return [0] * target
    if target >= frame_count:
        # Repeat nearest source snapshots so short histories still produce a
        # smooth, correctly timed clip without inventing interpolated positions.
        return [round(i * (frame_count - 1) / (target - 1)) for i in range(target)]
    return [round(i * (frame_count - 1) / (target - 1)) for i in range(target)]


def build_history(frames: Iterable[dict[str, Any]]) -> dict[str, list[tuple[datetime, float, float]]]:
    history: dict[str, list[tuple[datetime, float, float]]] = defaultdict(list)
    for frame in frames:
        dt = frame["timestamp"]
        for vessel in frame["vessels"]:
            key = str(vessel.get("mmsi") or short_name(vessel))
            history[key].append((dt, float(vessel["lat"]), float(vessel["lon"])))
    return history


def _configure_fonts(plt: Any) -> None:
    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK TC",
        "Noto Sans CJK SC",
        "WenQuanYi Zen Hei",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def _draw_static_map(ax: Any) -> None:
    from map_basemap import draw_land

    lat_min, lat_max, lon_min, lon_max = MAP_BOUNDS
    ax.set_facecolor(BG)
    draw_land(ax, MAP_BOUNDS, zorder=1, linewidth=0.7)

    # A restrained geographic frame: enough context for public-facing storytelling
    # without presenting the output as a precision navigation chart.
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([118, 120, 122])
    ax.set_yticks([22, 24, 26])
    ax.grid(True, color=GRID, alpha=0.55, linewidth=0.8)
    ax.tick_params(colors=MUTED, labelsize=12, length=0)
    for spine in ax.spines.values():
        spine.set_color(GRID)
        spine.set_linewidth(1.0)

    labels = [
        (23.7, 120.95, "TAIWAN"),
        (24.45, 118.35, "KINMEN"),
        (26.15, 119.95, "MATSU"),
        (23.55, 119.62, "PENGHU"),
        (24.1, 119.15, "TAIWAN STRAIT"),
    ]
    for lat, lon, label in labels:
        ax.text(lon, lat, label, color=MUTED, fontsize=11, alpha=0.8,
                ha="center", va="center", zorder=2)


def render_video(
    frames: list[dict[str, Any]],
    output: Path,
    duration: float = 20.0,
    fps: int = 30,
    trail_hours: float = 36.0,
    dpi: int = 100,
    preview: Path | None = None,
) -> Path:
    """Render an MP4 using Matplotlib + ffmpeg."""
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

    # 1080x1920 at dpi=100. A different dpi scales the pixel dimensions while
    # retaining the 9:16 composition.
    fig = plt.figure(figsize=(10.8, 19.2), dpi=dpi, facecolor=BG)
    ax = fig.add_axes([0.07, 0.20, 0.86, 0.58])
    _draw_static_map(ax)

    title = fig.text(0.07, 0.935, "CHINA COAST GUARD", color=TEXT,
                     fontsize=31, fontweight="bold", ha="left", va="top")
    fig.text(0.07, 0.900, "AIS ACTIVITY AROUND TAIWAN", color=ACCENT,
             fontsize=17, fontweight="bold", ha="left", va="top")
    fig.text(0.07, 0.865, "Public AIS observations • timeline playback",
             color=MUTED, fontsize=13, ha="left", va="top")

    date_text = fig.text(0.07, 0.825, "", color=TEXT, fontsize=27,
                         fontweight="bold", ha="left", va="top")
    time_text = fig.text(0.07, 0.795, "", color=MUTED, fontsize=15,
                         ha="left", va="top")
    count_text = fig.text(0.93, 0.825, "", color=TEXT, fontsize=25,
                          fontweight="bold", ha="right", va="top")
    count_label = fig.text(0.93, 0.795, "VESSELS OBSERVED", color=MUTED,
                           fontsize=11, ha="right", va="top")

    # Bottom timeline / disclosure block.
    line_bg = plt.Line2D([0.07, 0.93], [0.115, 0.115], transform=fig.transFigure,
                         color=GRID, linewidth=8, solid_capstyle="round")
    line_fg = plt.Line2D([0.07, 0.07], [0.115, 0.115], transform=fig.transFigure,
                         color=ACCENT, linewidth=8, solid_capstyle="round")
    fig.lines.extend([line_bg, line_fg])
    progress_text = fig.text(0.07, 0.085, "", color=MUTED, fontsize=12,
                             ha="left", va="top")
    fig.text(0.07, 0.052,
             "AIS can be absent, delayed, spoofed, or incomplete. This visualization is not a real-time tactical picture.",
             color=MUTED, fontsize=10.5, ha="left", va="top", wrap=True)
    fig.text(0.93, 0.024, "Taiwan Gray Zone Monitor", color=MUTED,
             fontsize=10.5, ha="right", va="bottom")

    indices = choose_frame_indices(len(frames), duration, fps)
    history = build_history(frames)
    source_start = frames[0]["timestamp"]
    source_end = frames[-1]["timestamp"]
    trail_delta = timedelta(hours=max(trail_hours, 1.0))
    artists: list[Any] = []
    stroke = [withStroke(linewidth=3.5, foreground=BG)]

    def clear_dynamic() -> None:
        while artists:
            artist = artists.pop()
            try:
                artist.remove()
            except ValueError:
                pass

    def draw_frame(source_idx: int, video_idx: int) -> None:
        clear_dynamic()
        frame = frames[source_idx]
        now = frame["timestamp"]
        vessels = frame["vessels"]
        visible_ids = set()

        # Trails are drawn from source observations only; no synthetic path
        # interpolation is introduced between AIS fixes.
        for vessel in vessels:
            key = str(vessel.get("mmsi") or short_name(vessel))
            visible_ids.add(key)
            pts = [p for p in history.get(key, []) if now - trail_delta <= p[0] <= now]
            if len(pts) >= 2:
                artists.append(ax.plot(
                    [p[2] for p in pts], [p[1] for p in pts],
                    color=TRAIL, linewidth=2.0, alpha=0.48, zorder=4,
                    solid_capstyle="round",
                )[0])

        # Current-position halo and point.
        if vessels:
            lons = [float(v["lon"]) for v in vessels]
            lats = [float(v["lat"]) for v in vessels]
            artists.append(ax.scatter(lons, lats, s=250, color=ACCENT,
                                      alpha=0.13, edgecolors="none", zorder=5))
            artists.append(ax.scatter(lons, lats, s=72, color=CCG,
                                      edgecolors=ACCENT, linewidths=1.6, zorder=6))

            # Label at most six vessels to keep a social-video frame readable.
            for vessel in vessels[:6]:
                label = short_name(vessel)
                txt = ax.annotate(
                    label,
                    (float(vessel["lon"]), float(vessel["lat"])),
                    xytext=(7, 7), textcoords="offset points",
                    color=TEXT, fontsize=9.5, fontweight="bold", zorder=7,
                )
                txt.set_path_effects(stroke)
                artists.append(txt)

        date_text.set_text(now.strftime("%Y · %m · %d"))
        time_text.set_text(now.strftime("%H:%M  UTC+8"))
        count_text.set_text(str(len(visible_ids)))
        elapsed_days = max(0, (now.date() - source_start.date()).days)
        total_days = max(1, (source_end.date() - source_start.date()).days + 1)
        progress_text.set_text(
            f"DAY {min(elapsed_days + 1, total_days)} / {total_days}   •   {source_start:%Y-%m-%d} → {source_end:%Y-%m-%d}"
        )
        progress = video_idx / max(1, len(indices) - 1)
        line_fg.set_xdata([0.07, 0.07 + 0.86 * progress])

    # Save a still using the final frame; useful as a thumbnail/social preview.
    if preview:
        draw_frame(indices[-1], len(indices) - 1)
        fig.savefig(preview, dpi=dpi, facecolor=BG)

    metadata = {
        "title": "China Coast Guard AIS Timeline Around Taiwan",
        "artist": "Taiwan Gray Zone Monitor",
        "comment": "Public AIS observations; not a real-time tactical picture.",
    }
    writer = FFMpegWriter(
        fps=max(1, fps),
        codec="libx264",
        bitrate=6000,
        metadata=metadata,
        extra_args=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
    )
    with writer.saving(fig, str(output), dpi=dpi):
        for video_idx, source_idx in enumerate(indices):
            draw_frame(source_idx, video_idx)
            writer.grab_frame(facecolor=BG)

    plt.close(fig)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a portrait CCG AIS timeline MP4")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help="Tier-1 AIS track history JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="Output MP4 path")
    parser.add_argument("--preview", type=Path, default=None,
                        help="Optional PNG thumbnail path")
    parser.add_argument("--days", type=int, default=14,
                        help="Trailing number of days to include (default: 14)")
    parser.add_argument("--duration", type=float, default=20.0,
                        help="Video duration in seconds (default: 20)")
    parser.add_argument("--fps", type=int, default=30,
                        help="Frames per second (default: 30)")
    parser.add_argument("--trail-hours", type=float, default=36.0,
                        help="How many hours of each vessel trail to retain")
    parser.add_argument("--dpi", type=int, default=100,
                        help="100 dpi = 1080x1920 output")
    args = parser.parse_args()

    if not args.input.exists():
        parser.error(f"input file not found: {args.input}")

    frames = load_frames(args.input, days=args.days)
    ccg_positions = sum(len(f["vessels"]) for f in frames)
    unique = {
        str(v.get("mmsi") or short_name(v))
        for f in frames for v in f["vessels"]
    }
    if not unique:
        raise SystemExit("No China Coast Guard AIS positions found; refusing to render an empty/misleading video")

    print(f"Loaded {len(frames)} snapshots, {ccg_positions} CCG positions, {len(unique)} unique vessels")
    out = render_video(
        frames,
        args.output,
        duration=args.duration,
        fps=args.fps,
        trail_hours=args.trail_hours,
        dpi=args.dpi,
        preview=args.preview,
    )
    print(f"✅ CCG timeline video: {out}")
    if args.preview:
        print(f"✅ Preview image: {args.preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
