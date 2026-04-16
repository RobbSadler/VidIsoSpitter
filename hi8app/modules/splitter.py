"""
Module 2 — Video Splitting

Detects cut points using two complementary FFmpeg methods, then merges
the results before slicing:

  • blackdetect  — finds the black frames that Hi-8 cameras produce when
                   the recording is stopped and restarted. Very reliable
                   for genuine tape scene changes.

  • scene score  — frame-difference score; catches abrupt content changes
                   (jump cuts, in-camera edits) that don't produce black
                   frames.

Both sets of timestamps are merged with a minimum-gap deduplication so
that the two detectors don't double-fire on the same boundary.
"""

import subprocess
from pathlib import Path

# Video file extensions recognised after splitting.
CLIP_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}

# blackdetect: minimum consecutive black duration (seconds) to count as a cut.
BLACK_MIN_DURATION = 0.05
# blackdetect: pixel luminance threshold (0–1) below which a pixel is "black".
BLACK_PIX_TH = 0.10

# scene-score threshold (0.0–1.0). Lower = more splits.
SCENE_THRESHOLD = 0.4

# Minimum gap (seconds) between two split points; closer timestamps are merged.
MIN_SPLIT_GAP = 2.0


# ---------------------------------------------------------------------------
# Detection helpers — each returns a list of timestamps in seconds
# ---------------------------------------------------------------------------

def _get_duration(clip_path: Path) -> float:
    """Return clip duration in seconds via ffprobe, or 0.0 on failure."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(clip_path),
        ],
        capture_output=True, text=True,
    )
    try:
        return round(float(result.stdout.strip()), 3)
    except ValueError:
        return 0.0


def _probe_duration(source_video: str) -> tuple[float | None, str]:
    """Return (duration_seconds, "") or (None, error_message)."""
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            source_video,
        ],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        return None, probe.stderr
    try:
        return float(probe.stdout.strip()), ""
    except ValueError:
        return None, f"Could not parse duration: {probe.stdout!r}"


def _detect_black_frames(source_video: str) -> list[float]:
    """Return timestamps (black_end) where tape stop/start gaps end."""
    result = subprocess.run(
        [
            "ffmpeg", "-i", source_video,
            "-vf", f"blackdetect=d={BLACK_MIN_DURATION}:pix_th={BLACK_PIX_TH}",
            "-an", "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    timestamps = []
    for line in result.stderr.splitlines():
        if "black_end:" in line:
            for token in line.split():
                if token.startswith("black_end:"):
                    try:
                        timestamps.append(float(token.split(":")[1]))
                    except ValueError:
                        pass
    return timestamps


def _detect_scene_changes(source_video: str) -> list[float]:
    """Return timestamps where the scene-difference score exceeds the threshold."""
    detect = subprocess.run(
        [
            "ffmpeg", "-i", source_video,
            "-filter:v", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
            "-vsync", "vfr",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    timestamps = []
    for line in detect.stderr.splitlines():
        if "pts_time:" in line:
            for token in line.split():
                if token.startswith("pts_time:"):
                    try:
                        timestamps.append(float(token.split(":")[1]))
                    except ValueError:
                        pass
    return timestamps


def _merge_timestamps(
    lists: list[list[float]],
    total_duration: float,
    min_gap: float = MIN_SPLIT_GAP,
) -> list[float]:
    """Combine multiple timestamp lists into a deduplicated, sorted sequence.

    Points that are closer than min_gap seconds are collapsed (the first
    one wins) so that both detectors firing on the same boundary don't
    create a near-zero-length clip.

    Returns a list that always starts with 0.0 and ends with total_duration.
    """
    all_ts = sorted(t for lst in lists for t in lst if 0 < t < total_duration)
    merged = []
    for t in all_ts:
        if not merged or (t - merged[-1]) >= min_gap:
            merged.append(t)
    return [0.0] + merged + [total_duration]


def generate_thumbnail(clip_path: Path, thumb_path: Path, duration: float) -> tuple[bool, str]:
    """Extract one frame at 2 s (or 0.5 s if clip is shorter) as a JPEG."""
    seek = 0.5 if duration < 2.0 else 2.0
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(seek),
            "-i", str(clip_path),
            "-vframes", "1",
            "-vf", "scale=320:240",
            str(thumb_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False, result.stderr
    return True, ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def split_project(project_data: dict, project_dir: Path) -> tuple[bool, dict]:
    """Detect cut points with both blackdetect and scene-score, then slice.

    Returns (True, {"clips": [...], "split_counts": {...}}) on success.
    Returns (False, {"error": "...", "stderr": "..."}) on failure.
    """
    source_video = project_data["source_video"]
    clips_dir = project_dir / "clips"
    thumbs_dir = project_dir / "thumbs"

    # --- Step 1: probe duration ---
    total_duration, err = _probe_duration(source_video)
    if total_duration is None:
        return False, {"error": "ffprobe failed to read source video", "stderr": err}

    # --- Step 2: run both detectors ---
    black_ts  = _detect_black_frames(source_video)
    scene_ts  = _detect_scene_changes(source_video)
    timestamps = _merge_timestamps([black_ts, scene_ts], total_duration)

    # --- Step 3: cut one segment per window ---
    errors = []
    for i, start in enumerate(timestamps[:-1]):
        end = timestamps[i + 1]
        out_file = clips_dir / f"clip_{i + 1:03d}.mp4"
        cut = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-to", str(end),
                "-i", source_video,
                "-c", "copy",
                str(out_file),
            ],
            capture_output=True, text=True,
        )
        if cut.returncode != 0:
            errors.append(cut.stderr)

    if errors:
        return False, {"error": "FFmpeg cut failed", "stderr": "\n".join(errors)}

    # --- Step 4: enumerate clips ---
    clip_files = sorted(
        f for f in clips_dir.iterdir()
        if f.is_file() and f.suffix.lower() in CLIP_EXTENSIONS
    )
    if not clip_files:
        return False, {"error": "No clip files found in clips/ after splitting", "stderr": ""}

    # --- Step 5: thumbnails + durations ---
    clips = []
    thumb_errors = []
    for i, clip_file in enumerate(clip_files):
        clip_id = f"clip_{i + 1:03d}"

        canonical = clips_dir / f"{clip_id}{clip_file.suffix.lower()}"
        if clip_file != canonical:
            clip_file = clip_file.rename(canonical)

        duration = _get_duration(clip_file)

        thumb_path = thumbs_dir / f"{clip_id}.jpg"
        ok, err = generate_thumbnail(clip_file, thumb_path, duration)
        if not ok:
            thumb_errors.append(f"{clip_file.name}: {err}")

        clips.append({
            "id": clip_id,
            "filename": clip_file.name,
            "duration_seconds": duration,
            "title": "",
            "deleted": False,
            "order": i + 1,
        })

    split_counts = {"blackdetect": len(black_ts), "scene": len(scene_ts), "total": len(clips)}

    result = {"clips": clips, "split_counts": split_counts}
    if thumb_errors:
        result["stderr"] = "\n".join(thumb_errors)
    return True, result
