"""
Module 5 — DVD Publishing Pipeline

Steps:
  1. Extract 3-second menu segments from each clip (FFmpeg)
  2. Concatenate segments into menu_bg.mp4 (FFmpeg)
  3. Generate menu overlay PNG (Pillow)
  4. Compose final menu video as MPEG-2 (FFmpeg)
  5. Re-encode clips to DVD-compliant MPEG-2 (FFmpeg, skipped if already MPEG-2)
  6. Generate dvdauthor XML
  7. Run dvdauthor (two passes)
  8. Create ISO with mkisofs
  9. Return iso_path
"""

import os
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Font paths tried in order for drawtext / Pillow rendering
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def _find_font() -> str | None:
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _clip_duration(path: Path) -> float:
    """Return clip duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _esc_drawtext(text: str) -> str:
    """Escape a string for use inside an FFmpeg drawtext filter value."""
    return text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def _run(cmd: list[str], env: dict | None = None) -> tuple[bool, str]:
    """Run a subprocess. Returns (success, stderr_or_stdout)."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    if result.returncode != 0:
        return False, result.stderr or result.stdout
    return True, result.stderr  # ffmpeg/dvdauthor log to stderr even on success


def _iso_volume_label(project_name: str) -> str:
    """Produce a valid ISO 9660 volume label: uppercase, underscores, max 32 chars."""
    label = project_name.upper().replace(" ", "_").replace("-", "_")
    # Strip characters outside [A-Z0-9_]
    label = "".join(c for c in label if c.isalnum() or c == "_")
    return label[:32]


def _is_mpeg2(clip_path: Path) -> bool:
    """Return True if the clip's video stream is already MPEG-2."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(clip_path),
        ],
        capture_output=True, text=True,
    )
    return result.stdout.strip().lower() == "mpeg2video"


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def _step1_menu_segments(
    clips: list[dict], clips_dir: Path, menu_dir: Path
) -> tuple[bool, list[Path], str]:
    """Extract the first 3 s of each clip as a DVD-resolution segment."""
    segments: list[Path] = []
    for i, clip in enumerate(clips):
        src = clips_dir / clip["filename"]
        seg = menu_dir / f"seg_{i + 1:03d}.mp4"
        ok, err = _run([
            "ffmpeg", "-y",
            "-i", str(src),
            "-t", "3",
            "-vf", "scale=720:480",
            "-r", "29.97",
            str(seg),
        ])
        if not ok:
            return False, [], err
        segments.append(seg)
    return True, segments, ""


def _step2_concat_segments(
    segments: list[Path], menu_dir: Path
) -> tuple[bool, Path, str]:
    """Concatenate menu segments into a single background video."""
    concat_txt = menu_dir / "concat.txt"
    lines = [f"file '{seg.name}'\n" for seg in segments]
    concat_txt.write_text("".join(lines))

    bg = menu_dir / "menu_bg.mp4"
    ok, err = _run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_txt),
        "-c", "copy",
        str(bg),
    ])
    return ok, bg, err


def _step3_overlay_image(
    project_name: str, clips: list[dict], menu_dir: Path
) -> Path:
    """Draw a semi-transparent menu overlay PNG with Pillow."""
    img = Image.new("RGBA", (720, 480), (0, 0, 0, 180))
    draw = ImageDraw.Draw(img)

    # Try to load a system font; fall back to Pillow's default bitmap font
    font_title = font_body = None
    font_path = _find_font()
    if font_path:
        try:
            font_title = ImageFont.truetype(font_path, 36)
            font_body  = ImageFont.truetype(font_path, 24)
        except OSError:
            pass

    draw.text((40, 30), project_name, fill="white", font=font_title)
    for i, clip in enumerate(clips):
        label = clip.get("title") or f"Clip {i + 1}"
        draw.text((60, 110 + i * 42), f"{i + 1}. {label}", fill="white", font=font_body)

    out = menu_dir / "menu_overlay.png"
    img.save(str(out))
    return out


def _step4_compose_menu(
    bg: Path, overlay: Path, menu_dir: Path
) -> tuple[bool, Path, str]:
    """Burn the overlay into the background and encode as DVD MPEG-2."""
    final = menu_dir / "menu_final.mpg"
    ok, err = _run([
        "ffmpeg", "-y",
        "-i", str(bg),
        "-i", str(overlay),
        "-filter_complex", "overlay=0:0",
        "-c:v", "mpeg2video", "-b:v", "4000k",
        "-c:a", "ac3", "-ar", "48000",
        str(final),
    ])
    return ok, final, err


def _step5_encode_clips(
    clips: list[dict],
    clips_dir: Path,
    dvd_clips_dir: Path,
    fade_duration: float = 0.0,
) -> tuple[bool, list[Path], str]:
    """Re-encode clips to DVD-compliant MPEG-2.

    If fade_duration > 0 each clip gets a fade-in at the start and a
    fade-out at the end (audio and video).  Fades force a full re-encode
    even for clips that are already MPEG-2.
    """
    dvd_clips_dir.mkdir(parents=True, exist_ok=True)
    encoded: list[Path] = []
    for clip in clips:
        src = clips_dir / clip["filename"]
        dst = dvd_clips_dir / f"{clip['id']}.mpg"

        if fade_duration > 0:
            dur = _clip_duration(src)
            fade_out_start = max(0.0, dur - fade_duration)
            vf = (
                f"scale=720:480,"
                f"fade=t=in:st=0:d={fade_duration},"
                f"fade=t=out:st={fade_out_start}:d={fade_duration}"
            )
            af = (
                f"afade=t=in:st=0:d={fade_duration},"
                f"afade=t=out:st={fade_out_start}:d={fade_duration}"
            )
            ok, err = _run([
                "ffmpeg", "-y", "-i", str(src),
                "-vf", vf, "-af", af,
                "-r", "29.97",
                "-c:v", "mpeg2video", "-b:v", "5000k",
                "-c:a", "ac3", "-ar", "48000",
                str(dst),
            ])
        elif _is_mpeg2(src):
            ok, err = _run([
                "ffmpeg", "-y", "-i", str(src),
                "-c:v", "copy", "-c:a", "ac3", "-ar", "48000",
                str(dst),
            ])
        else:
            ok, err = _run([
                "ffmpeg", "-y", "-i", str(src),
                "-vf", "scale=720:480", "-r", "29.97",
                "-c:v", "mpeg2video", "-b:v", "5000k",
                "-c:a", "ac3", "-ar", "48000",
                str(dst),
            ])
        if not ok:
            return False, [], err
        encoded.append(dst)
    return True, encoded, ""


def _step5b_generate_title_cards(
    clips: list[dict],
    dvd_clips_dir: Path,
    title_duration: float = 3.0,
    fade_duration: float = 0.5,
) -> tuple[bool, dict[str, Path], str]:
    """Generate a black title-card MPG for each clip using FFmpeg drawtext.

    Returns (success, {clip_id: Path}, stderr_on_failure).
    The chapter marker is placed on the title card, so DVD navigation lands
    there first before the clip content plays.
    """
    dvd_clips_dir.mkdir(parents=True, exist_ok=True)
    font = _find_font()
    fade_out_start = max(0.0, title_duration - fade_duration)
    paths: dict[str, Path] = {}

    for clip in clips:
        title = clip.get("title") or f"Clip {clip['id']}"
        out = dvd_clips_dir / f"title_{clip['id']}.mpg"

        dt = (
            f"drawtext=text='{_esc_drawtext(title)}'"
            f":fontcolor=white:fontsize=42"
            f":x=(w-text_w)/2:y=(h-text_h)/2"
        )
        if font:
            dt += f":fontfile='{font}'"

        vf = (
            f"{dt},"
            f"fade=t=in:st=0:d={fade_duration},"
            f"fade=t=out:st={fade_out_start}:d={fade_duration}"
        )

        ok, err = _run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=720x480:r=29.97",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-vf", vf,
            "-t", str(title_duration),
            "-c:v", "mpeg2video", "-b:v", "5000k",
            "-c:a", "ac3", "-ar", "48000",
            "-shortest", str(out),
        ])
        if not ok:
            return False, {}, err
        paths[clip["id"]] = out

    return True, paths, ""


def _step6_dvdauthor_xml(
    project_name: str,
    clips: list[dict],
    output_dir: Path,
    dvd_clips_dir: Path,
    menu_final: Path,
    title_card_paths: dict[str, Path] | None = None,
) -> Path:
    """Write the dvdauthor XML file and return its path.

    Each clip becomes one DVD chapter.  When title cards are present the
    chapter marker sits on the title card VOB so that navigation lands on
    the title first; the clip content VOB immediately follows with no
    separate chapter marker.
    """
    dvd_dir = output_dir / "dvd"
    vob_lines = []
    for c in clips:
        clip_file = dvd_clips_dir / (c["id"] + ".mpg")
        if title_card_paths and c["id"] in title_card_paths:
            vob_lines.append(
                f'        <vob file="{title_card_paths[c["id"]]}" chapters="0"/>'
            )
            vob_lines.append(f'        <vob file="{clip_file}"/>')
        else:
            vob_lines.append(f'        <vob file="{clip_file}" chapters="0"/>')
    vobs = "\n".join(vob_lines)
    xml = f"""<dvdauthor dest="{dvd_dir}">
  <vmgm>
    <menus>
      <pgc entry="title">
        <vob file="{menu_final}" pause="inf"/>
      </pgc>
    </menus>
  </vmgm>
  <titleset>
    <titles>
      <pgc>
{vobs}
      </pgc>
    </titles>
  </titleset>
</dvdauthor>"""
    xml_path = output_dir / "dvd.xml"
    xml_path.write_text(xml)
    return xml_path


def _step7_dvdauthor(xml_path: Path, dvd_dir: Path) -> tuple[bool, str]:
    """Run dvdauthor (structure pass + table-of-contents pass)."""
    dvd_dir.mkdir(parents=True, exist_ok=True)
    env = {"VIDEO_FORMAT": "NTSC"}

    ok, err = _run(["dvdauthor", "-o", str(dvd_dir), "-x", str(xml_path)], env=env)
    if not ok:
        return False, err

    ok, err = _run(["dvdauthor", "-T", "-o", str(dvd_dir)], env=env)
    return ok, err


def _step8_mkisofs(
    project_name: str, dvd_dir: Path, output_dir: Path
) -> tuple[bool, Path, str]:
    """Create the final ISO image."""
    label = _iso_volume_label(project_name)
    iso_path = output_dir / f"{label}.iso"
    ok, err = _run([
        "mkisofs",
        "-dvd-video",
        "-V", label,
        "-o", str(iso_path),
        str(dvd_dir),
    ])
    return ok, iso_path, err


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def publish_project(project_data: dict, project_dir: Path) -> tuple[bool, dict]:
    """Run the full DVD publishing pipeline.

    Reads project_data["output_settings"] for transition configuration:
      transition     : "none" | "fade" | "title"  (default "none")
      fade_duration  : seconds for fade in/out     (default 0.5)
      title_duration : seconds for title card      (default 3.0)

    Returns (True, {"iso_path": "..."}) on success.
    Returns (False, {"error": "...", "step": "...", "stderr": "..."}) on failure.
    """
    project_name = project_data["project_name"]
    clips = [c for c in project_data.get("clips", []) if not c.get("deleted")]
    clips.sort(key=lambda c: c.get("order", 0))

    if not clips:
        return False, {"error": "No clips to publish", "step": "validation", "stderr": ""}

    out_cfg       = project_data.get("output_settings", {})
    transition    = out_cfg.get("transition", "none")
    fade_duration = float(out_cfg.get("fade_duration", 0.5))
    title_duration = float(out_cfg.get("title_duration", 3.0))

    clips_dir     = project_dir / "clips"
    menu_dir      = project_dir / "menu"
    output_dir    = project_dir / "output"
    dvd_clips_dir = output_dir / "dvd_clips"
    dvd_dir       = output_dir / "dvd"

    menu_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    # Step 1
    ok, segments, err = _step1_menu_segments(clips, clips_dir, menu_dir)
    if not ok:
        return False, {"error": "Failed to extract menu segments", "step": "menu_segments", "stderr": err}

    # Step 2
    ok, bg, err = _step2_concat_segments(segments, menu_dir)
    if not ok:
        return False, {"error": "Failed to concatenate menu segments", "step": "concat_segments", "stderr": err}

    # Step 3 (Pillow — cannot fail fatally)
    overlay = _step3_overlay_image(project_name, clips, menu_dir)

    # Step 4
    ok, menu_final, err = _step4_compose_menu(bg, overlay, menu_dir)
    if not ok:
        return False, {"error": "Failed to compose menu video", "step": "compose_menu", "stderr": err}

    # Step 5 — encode clips (with fades if requested)
    apply_fade = transition in ("fade", "title")
    ok, _, err = _step5_encode_clips(
        clips, clips_dir, dvd_clips_dir,
        fade_duration=fade_duration if apply_fade else 0.0,
    )
    if not ok:
        return False, {"error": "Failed to encode clips to DVD format", "step": "encode_clips", "stderr": err}

    # Step 5b — generate title cards if requested
    title_card_paths = None
    if transition == "title":
        ok, title_card_paths, err = _step5b_generate_title_cards(
            clips, dvd_clips_dir, title_duration, fade_duration,
        )
        if not ok:
            return False, {"error": "Failed to generate title cards", "step": "title_cards", "stderr": err}

    # Step 6
    xml_path = _step6_dvdauthor_xml(
        project_name, clips, output_dir, dvd_clips_dir, menu_final,
        title_card_paths=title_card_paths,
    )

    # Step 7
    ok, err = _step7_dvdauthor(xml_path, dvd_dir)
    if not ok:
        return False, {"error": "dvdauthor failed", "step": "dvdauthor", "stderr": err}

    # Step 8
    ok, iso_path, err = _step8_mkisofs(project_name, dvd_dir, output_dir)
    if not ok:
        return False, {"error": "mkisofs failed", "step": "mkisofs", "stderr": err}

    return True, {"iso_path": str(iso_path.resolve())}
