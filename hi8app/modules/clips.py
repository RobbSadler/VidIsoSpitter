"""
Module 3 — Clip Management

All functions receive the loaded project_data dict and project_dir Path.
They return (updated_project_data, response_payload) or raise nothing —
errors are surfaced as (None, {"error": "..."}, status_code) tuples.
"""

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from modules.splitter import _get_duration, generate_thumbnail


def _with_urls(clip: dict, sanitized_name: str) -> dict:
    """Return a copy of a clip dict augmented with thumbnail_url and stream_url."""
    clip_id = clip["id"]
    base = f"/api/project/{sanitized_name}"
    return {
        **clip,
        "thumbnail_url": f"{base}/thumbs/{clip_id}.jpg",
        "stream_url": f"{base}/stream/{clip_id}",
    }


def list_clips(project_data: dict, sanitized_name: str) -> list[dict]:
    """Return all non-deleted clips sorted by order, with URL fields added."""
    active = [c for c in project_data.get("clips", []) if not c.get("deleted")]
    active.sort(key=lambda c: c.get("order", 0))
    return [_with_urls(c, sanitized_name) for c in active]


def update_clip_title(
    project_data: dict, sanitized_name: str, clip_id: str, title: str
) -> tuple[dict | None, dict, int]:
    """Set title on a clip. Returns (updated_project_data, payload, status_code)."""
    if not title:
        return None, {"error": "title must not be empty"}, 400

    clips = project_data.get("clips", [])
    for clip in clips:
        if clip["id"] == clip_id:
            clip["title"] = title
            return project_data, _with_urls(clip, sanitized_name), 200

    return None, {"error": f"Clip '{clip_id}' not found"}, 404


def reorder_clips(
    project_data: dict, order: list[str]
) -> tuple[dict | None, dict, int]:
    """Assign new order values based on the provided clip-id list."""
    clips = project_data.get("clips", [])
    index = {c["id"]: c for c in clips}

    unknown = [cid for cid in order if cid not in index]
    if unknown:
        return None, {"error": f"Unknown clip ids: {unknown}"}, 400

    for position, clip_id in enumerate(order, start=1):
        index[clip_id]["order"] = position

    return project_data, {"reordered": True, "order": order}, 200


def delete_clip(
    project_data: dict, project_dir: Path, clip_id: str
) -> tuple[dict | None, dict, int]:
    """Soft-delete a clip: move files to deleted/ and mark deleted=True."""
    clips = project_data.get("clips", [])
    target = next((c for c in clips if c["id"] == clip_id), None)
    if target is None:
        return None, {"error": f"Clip '{clip_id}' not found"}, 404

    if target.get("deleted"):
        return None, {"error": f"Clip '{clip_id}' is already deleted"}, 400

    deleted_dir = project_dir / "deleted"

    # Move video file
    clip_src = project_dir / "clips" / target["filename"]
    if clip_src.exists():
        shutil.move(str(clip_src), str(deleted_dir / target["filename"]))

    # Move thumbnail file
    thumb_src = project_dir / "thumbs" / f"{clip_id}.jpg"
    if thumb_src.exists():
        shutil.move(str(thumb_src), str(deleted_dir / f"{clip_id}.jpg"))

    target["deleted"] = True
    return project_data, {"deleted": True, "clip_id": clip_id}, 200


def split_clip_at_time(
    project_data: dict, project_dir: Path, sanitized_name: str,
    clip_id: str, at: float,
) -> tuple[dict | None, dict, int]:
    """Split a clip at *at* seconds into two consecutive clips.

    The original clip becomes part A (0 → at).
    A new clip record is inserted immediately after it for part B (at → end).
    Both clips get fresh thumbnails and the full order sequence is renumbered.
    """
    clips = project_data.get("clips", [])
    target = next((c for c in clips if c["id"] == clip_id and not c.get("deleted")), None)
    if target is None:
        return None, {"error": f"Clip '{clip_id}' not found"}, 404

    clips_dir  = project_dir / "clips"
    thumbs_dir = project_dir / "thumbs"
    src = clips_dir / target["filename"]

    if not src.is_file():
        return None, {"error": "Clip file not found on disk"}, 500

    duration = _get_duration(src)
    if at <= 0 or at >= duration:
        return None, {
            "error": f"Split point {at:.2f}s is outside the clip (duration {duration:.2f}s)"
        }, 400

    ext    = src.suffix.lower()
    new_id = f"clip_{uuid.uuid4().hex[:8]}"
    part_a = clips_dir / f"_split_a{ext}"
    part_b = clips_dir / f"{new_id}{ext}"

    # ── Part A: 0 → at ──────────────────────────────────────
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-to", str(at), "-c", "copy", str(part_a)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        part_a.unlink(missing_ok=True)
        return None, {"error": "ffmpeg failed splitting part A", "stderr": r.stderr}, 500

    # ── Part B: at → end ────────────────────────────────────
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ss", str(at), "-c", "copy", str(part_b)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        part_a.unlink(missing_ok=True)
        part_b.unlink(missing_ok=True)
        return None, {"error": "ffmpeg failed splitting part B", "stderr": r.stderr}, 500

    # Replace original file with part A
    src.unlink()
    part_a.rename(src)

    # Update clip A metadata
    target["duration_seconds"] = _get_duration(src)
    generate_thumbnail(src, thumbs_dir / f"{clip_id}.jpg", target["duration_seconds"])

    # Build clip B record with order just after clip A
    dur_b = _get_duration(part_b)
    generate_thumbnail(part_b, thumbs_dir / f"{new_id}.jpg", dur_b)
    clip_b = {
        "id":               new_id,
        "filename":         part_b.name,
        "duration_seconds": dur_b,
        "title":            "",
        "deleted":          False,
        "order":            target["order"] + 0.5,   # fractional → sorts after A
    }
    clips.append(clip_b)

    # Renumber all active clips by current sort order
    active = sorted([c for c in clips if not c.get("deleted")], key=lambda c: c["order"])
    for i, c in enumerate(active, start=1):
        c["order"] = i

    return project_data, {
        "clip_a": _with_urls(target, sanitized_name),
        "clip_b": _with_urls(clip_b, sanitized_name),
    }, 200


def merge_clips(
    project_data: dict, project_dir: Path, sanitized_name: str, clip_id: str
) -> tuple[dict | None, dict, int]:
    """Losslessly merge clip_id with the next clip in order.

    The two source files are concatenated with ffmpeg concat demuxer (no
    re-encode), saved under the first clip's filename, and the second clip
    is soft-deleted.  A new thumbnail is generated for the merged clip.

    Returns (updated_project_data, payload, status_code).
    """
    active = [c for c in project_data.get("clips", []) if not c.get("deleted")]
    active.sort(key=lambda c: c.get("order", 0))

    ids = [c["id"] for c in active]
    if clip_id not in ids:
        return None, {"error": f"Clip '{clip_id}' not found"}, 404

    idx = ids.index(clip_id)
    if idx + 1 >= len(active):
        return None, {"error": "No next clip to merge into"}, 400

    clip_a = active[idx]
    clip_b = active[idx + 1]

    clips_dir  = project_dir / "clips"
    thumbs_dir = project_dir / "thumbs"
    deleted_dir = project_dir / "deleted"

    path_a = clips_dir / clip_a["filename"]
    path_b = clips_dir / clip_b["filename"]

    if not path_a.is_file():
        return None, {"error": f"File missing for clip {clip_a['id']}"}, 500
    if not path_b.is_file():
        return None, {"error": f"File missing for clip {clip_b['id']}"}, 500

    # Write a concat list to a temp file then merge into a temp output,
    # then atomically replace path_a so we never corrupt either source.
    merged_tmp = clips_dir / f"_merge_tmp{path_a.suffix}"
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, dir=clips_dir
        ) as flist:
            flist.write(f"file '{path_a.name}'\n")
            flist.write(f"file '{path_b.name}'\n")
            list_path = Path(flist.name)

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_path),
                "-c", "copy",
                str(merged_tmp),
            ],
            capture_output=True, text=True,
        )
        list_path.unlink(missing_ok=True)

        if result.returncode != 0:
            merged_tmp.unlink(missing_ok=True)
            return None, {"error": "ffmpeg concat failed", "stderr": result.stderr}, 500

        # Replace clip_a's file with the merged result
        path_a.unlink()
        merged_tmp.rename(path_a)

    except Exception as exc:
        merged_tmp.unlink(missing_ok=True)
        return None, {"error": str(exc)}, 500

    # Soft-delete clip_b's files
    if path_b.exists():
        shutil.move(str(path_b), str(deleted_dir / path_b.name))
    thumb_b = thumbs_dir / f"{clip_b['id']}.jpg"
    if thumb_b.exists():
        shutil.move(str(thumb_b), str(deleted_dir / thumb_b.name))

    # Update clip_a duration and regenerate thumbnail
    clip_a["duration_seconds"] = _get_duration(path_a)
    thumb_a = thumbs_dir / f"{clip_a['id']}.jpg"
    generate_thumbnail(path_a, thumb_a, clip_a["duration_seconds"])

    # Mark clip_b deleted in project data
    all_clips = project_data.get("clips", [])
    for c in all_clips:
        if c["id"] == clip_b["id"]:
            c["deleted"] = True
            break

    return project_data, _with_urls(clip_a, sanitized_name), 200
