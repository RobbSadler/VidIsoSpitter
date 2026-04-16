import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from modules.clips import delete_clip, list_clips, merge_clips, reorder_clips, update_clip_title
from modules.publisher import publish_project
from modules.splitter import split_project
import utils
from utils import list_projects, load_project, sanitize_name, save_project
from utils import load_settings, save_settings

app = Flask(__name__)

# Load persisted settings and apply projects_dir if one has been saved
_settings = load_settings()
if _settings.get("projects_dir"):
    utils.PROJECTS_DIR = Path(_settings["projects_dir"])

# Subdirectories created inside every project folder
PROJECT_SUBDIRS = ["source", "clips", "thumbs", "deleted", "menu", "output"]


def _is_valid_project_name(name: str) -> bool:
    """Reject characters that are illegal in Windows/Linux directory names."""
    return bool(name) and not name.isspace() and not re.search(r'[\\/:*?"<>|]', name)


# ---------------------------------------------------------------------------
# GET / and GET /api
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api")
def api_health():
    return jsonify({"status": "ok", "routes": [
        "POST /api/project/create",
        "GET  /api/projects",
        "POST /api/project/<project_name>/split",
        "GET  /api/project/<project_name>/clips",
        "PATCH /api/project/<project_name>/clips/<clip_id>",
        "POST /api/project/<project_name>/clips/reorder",
        "DELETE /api/project/<project_name>/clips/<clip_id>",
        "GET  /api/project/<project_name>/thumbs/<filename>",
        "GET  /api/project/<project_name>/stream/<clip_id>",
        "POST /api/project/<project_name>/publish",
    ]})


# ---------------------------------------------------------------------------
# GET  /api/settings
# POST /api/settings
# ---------------------------------------------------------------------------

@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify({
        "projects_dir": str(utils.PROJECTS_DIR) if utils.PROJECTS_DIR else None,
        "is_default": not load_settings().get("projects_dir"),
    }), 200


@app.route("/api/settings", methods=["POST"])
def post_settings():
    body = request.get_json(silent=True) or {}
    projects_dir = body.get("projects_dir", "").strip()
    if not projects_dir:
        return jsonify({"error": "projects_dir is required"}), 400
    p = Path(projects_dir)
    if not p.is_absolute():
        return jsonify({"error": "projects_dir must be an absolute path"}), 400
    p.mkdir(parents=True, exist_ok=True)
    utils.PROJECTS_DIR = p
    save_settings({"projects_dir": str(p)})
    return jsonify({"projects_dir": str(p)}), 200


# ---------------------------------------------------------------------------
# GET /api/browse_dir  — opens a native folder-picker dialog, returns path
# ---------------------------------------------------------------------------

@app.route("/api/browse_dir", methods=["GET"])
def browse_dir():
    import subprocess
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$d.Description = 'Select projects folder'; "
        "$d.ShowNewFolderButton = $true; "
        "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath } else { '' }"
    )
    try:
        ps = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=120,
        )
        win_path = ps.stdout.strip()
    except FileNotFoundError:
        return jsonify({"error": "powershell.exe not found"}), 500

    if not win_path:
        return jsonify({"path": None}), 200

    wsl = subprocess.run(["wslpath", win_path], capture_output=True, text=True)
    path = wsl.stdout.strip() if wsl.returncode == 0 else win_path
    return jsonify({"path": path}), 200


# ---------------------------------------------------------------------------
# GET /api/browse  — opens a native file-picker dialog, returns chosen path
# ---------------------------------------------------------------------------

@app.route("/api/browse", methods=["GET"])
def browse_file():
    import subprocess
    # Use a Windows file-picker dialog via PowerShell (works from WSL)
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$d = New-Object System.Windows.Forms.OpenFileDialog; "
        "$d.Filter = 'Video files|*.avi;*.mp4;*.mov;*.mkv;*.mpg;*.mpeg;*.m4v;*.wmv|All files|*.*'; "
        "$d.Title = 'Select source video'; "
        "if ($d.ShowDialog() -eq 'OK') { $d.FileName } else { '' }"
    )
    try:
        ps = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=120,
        )
        win_path = ps.stdout.strip()
    except FileNotFoundError:
        return jsonify({"error": "powershell.exe not found — cannot open file dialog"}), 500

    if not win_path:
        return jsonify({"path": None}), 200

    # Convert Windows path (e.g. E:\foo\bar.avi) to a WSL path (/mnt/e/foo/bar.avi)
    wsl = subprocess.run(["wslpath", win_path], capture_output=True, text=True)
    path = wsl.stdout.strip() if wsl.returncode == 0 else win_path
    return jsonify({"path": path}), 200


# ---------------------------------------------------------------------------
# GET /api/preview_source?path=...  — stream a local video file for preview
# ---------------------------------------------------------------------------

_PREVIEW_MIMETYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".wmv": "video/x-ms-wmv",
}


@app.route("/api/preview_source", methods=["GET"])
def preview_source():
    path = request.args.get("path", "").strip()
    if not path or not os.path.isabs(path):
        return jsonify({"error": "Invalid path"}), 400
    if not os.path.isfile(path):
        return jsonify({"error": "File not found"}), 404
    mimetype = _PREVIEW_MIMETYPES.get(Path(path).suffix.lower(), "video/mp4")
    return send_file(path, mimetype=mimetype, conditional=True)


# ---------------------------------------------------------------------------
# POST /api/project/create
# ---------------------------------------------------------------------------

@app.route("/api/project/create", methods=["POST"])
def create_project():
    body = request.get_json(silent=True) or {}

    source_video = body.get("source_video", "").strip()
    project_name = body.get("project_name", "").strip()

    # --- validation ---
    if not source_video or not project_name:
        return jsonify({"error": "source_video and project_name are required"}), 400

    if not _is_valid_project_name(project_name):
        return jsonify({"error": r'project_name must not contain \ / : * ? " < > |'}), 400

    if not os.path.isabs(source_video):
        return jsonify({"error": "source_video must be an absolute path"}), 400

    if not os.path.isfile(source_video):
        return jsonify({"error": "source_video path does not exist or is not a file"}), 400

    sanitized = sanitize_name(project_name)
    project_dir = utils.PROJECTS_DIR / sanitized

    # --- idempotency: return existing project if folder already exists ---
    if project_dir.exists():
        existing = load_project(sanitized)
        if existing:
            existing["working_dir"] = str(project_dir.resolve())
            return jsonify(existing), 409
        # Folder exists but no valid project.json — treat as collision too
        return jsonify({"error": "A project folder with that name already exists but has no valid project.json"}), 409

    # --- create directory structure ---
    for subdir in PROJECT_SUBDIRS:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    # --- write project.json ---
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    project_data = {
        "project_id": str(uuid.uuid4()),
        "project_name": project_name,
        "source_video": source_video,
        "created_at": now,
        "status": "created",
        "clips": [],
    }
    save_project(sanitized, project_data)

    response = {
        "project_id": project_data["project_id"],
        "project_name": project_data["project_name"],
        "working_dir": str(project_dir.resolve()),
        "status": "created",
    }
    return jsonify(response), 200


# ---------------------------------------------------------------------------
# DELETE /api/project/<project_name>
# ---------------------------------------------------------------------------

@app.route("/api/project/<project_name>", methods=["DELETE"])
def delete_project(project_name):
    import shutil
    sanitized = sanitize_name(project_name)
    project_dir = utils.PROJECTS_DIR / sanitized
    if not project_dir.is_dir():
        return jsonify({"error": f"Project '{project_name}' not found"}), 404
    shutil.rmtree(project_dir)
    return jsonify({"deleted": True, "project_name": project_name}), 200


# ---------------------------------------------------------------------------
# PATCH /api/project/<project_name>/rename
# ---------------------------------------------------------------------------

@app.route("/api/project/<project_name>/rename", methods=["PATCH"])
def rename_project(project_name):
    sanitized = sanitize_name(project_name)
    project_data = load_project(sanitized)
    if project_data is None:
        return jsonify({"error": f"Project '{project_name}' not found"}), 404

    body = request.get_json(silent=True) or {}
    new_name = body.get("project_name", "").strip()

    if not new_name:
        return jsonify({"error": "project_name is required"}), 400
    if not _is_valid_project_name(new_name):
        return jsonify({"error": r'project_name must not contain \ / : * ? " < > |'}), 400

    new_sanitized = sanitize_name(new_name)
    old_dir = utils.PROJECTS_DIR / sanitized
    new_dir = utils.PROJECTS_DIR / new_sanitized

    if new_sanitized != sanitized:
        if new_dir.exists():
            return jsonify({"error": "A project with that name already exists"}), 409
        old_dir.rename(new_dir)

    project_data["project_name"] = new_name
    save_project(new_sanitized, project_data)

    return jsonify({"project_name": new_name, "sanitized_name": new_sanitized}), 200


# ---------------------------------------------------------------------------
# POST /api/project/<project_name>/split
# ---------------------------------------------------------------------------

@app.route("/api/project/<project_name>/split", methods=["POST"])
def split(project_name):
    sanitized = sanitize_name(project_name)
    project_data = load_project(sanitized)
    if project_data is None:
        return jsonify({"error": f"Project '{project_name}' not found"}), 404

    if project_data.get("status") != "created":
        return jsonify({"error": "Project has already been split. Delete all clips first to re-split."}), 409

    project_dir = utils.PROJECTS_DIR / sanitized
    ok, payload = split_project(project_data, project_dir)
    if not ok:
        return jsonify(payload), 500

    project_data["status"] = "clips_ready"
    project_data["clips"] = payload["clips"]
    save_project(sanitized, project_data)

    return jsonify({
        "status": "clips_ready",
        "clip_count": len(payload["clips"]),
        "clips": payload["clips"],
    }), 200


# ---------------------------------------------------------------------------
# MODULE 3 — Clip Management
# ---------------------------------------------------------------------------

@app.route("/api/project/<project_name>/clips", methods=["GET"])
def get_clips(project_name):
    sanitized = sanitize_name(project_name)
    project_data = load_project(sanitized)
    if project_data is None:
        return jsonify({"error": f"Project '{project_name}' not found"}), 404
    return jsonify({"clips": list_clips(project_data, sanitized)}), 200


@app.route("/api/project/<project_name>/clips/reorder", methods=["POST"])
def reorder(project_name):
    sanitized = sanitize_name(project_name)
    project_data = load_project(sanitized)
    if project_data is None:
        return jsonify({"error": f"Project '{project_name}' not found"}), 404

    body = request.get_json(silent=True) or {}
    order = body.get("order")
    if not isinstance(order, list):
        return jsonify({"error": "'order' must be a list of clip ids"}), 400

    updated, payload, status = reorder_clips(project_data, order)
    if updated:
        save_project(sanitized, updated)
    return jsonify(payload), status


@app.route("/api/project/<project_name>/clips/<clip_id>", methods=["PATCH"])
def patch_clip(project_name, clip_id):
    sanitized = sanitize_name(project_name)
    project_data = load_project(sanitized)
    if project_data is None:
        return jsonify({"error": f"Project '{project_name}' not found"}), 404

    body = request.get_json(silent=True) or {}
    title = body.get("title", "").strip()

    updated, payload, status = update_clip_title(project_data, sanitized, clip_id, title)
    if updated:
        save_project(sanitized, updated)
    return jsonify(payload), status


@app.route("/api/project/<project_name>/clips/<clip_id>/merge_next", methods=["POST"])
def merge_clip_next(project_name, clip_id):
    sanitized = sanitize_name(project_name)
    project_data = load_project(sanitized)
    if project_data is None:
        return jsonify({"error": f"Project '{project_name}' not found"}), 404

    project_dir = utils.PROJECTS_DIR / sanitized
    updated, payload, status = merge_clips(project_data, project_dir, sanitized, clip_id)
    if updated:
        save_project(sanitized, updated)
    return jsonify(payload), status


@app.route("/api/project/<project_name>/clips/<clip_id>", methods=["DELETE"])
def soft_delete_clip(project_name, clip_id):
    sanitized = sanitize_name(project_name)
    project_data = load_project(sanitized)
    if project_data is None:
        return jsonify({"error": f"Project '{project_name}' not found"}), 404

    project_dir = utils.PROJECTS_DIR / sanitized
    updated, payload, status = delete_clip(project_data, project_dir, clip_id)
    if updated:
        save_project(sanitized, updated)
    return jsonify(payload), status


@app.route("/api/project/<project_name>/thumbs/<filename>", methods=["GET"])
def serve_thumb(project_name, filename):
    sanitized = sanitize_name(project_name)
    thumb_path = (utils.PROJECTS_DIR / sanitized / "thumbs" / filename).resolve()
    # Guard against path traversal
    allowed_dir = (utils.PROJECTS_DIR / sanitized / "thumbs").resolve()
    if not str(thumb_path).startswith(str(allowed_dir)):
        return jsonify({"error": "Invalid path"}), 400
    if not thumb_path.is_file():
        return jsonify({"error": "Thumbnail not found"}), 404
    return send_file(thumb_path, mimetype="image/jpeg")


@app.route("/api/project/<project_name>/stream/<clip_id>", methods=["GET"])
def stream_clip(project_name, clip_id):
    sanitized = sanitize_name(project_name)
    project_data = load_project(sanitized)
    if project_data is None:
        return jsonify({"error": f"Project '{project_name}' not found"}), 404

    clip = next(
        (c for c in project_data.get("clips", [])
         if c["id"] == clip_id and not c.get("deleted")),
        None,
    )
    if clip is None:
        return jsonify({"error": f"Clip '{clip_id}' not found"}), 404

    clip_path = (utils.PROJECTS_DIR / sanitized / "clips" / clip["filename"]).resolve()
    if not clip_path.is_file():
        return jsonify({"error": "Clip file missing from disk"}), 404
    return send_file(clip_path, mimetype="video/mp4", conditional=True)


# ---------------------------------------------------------------------------
# POST /api/project/<project_name>/publish
# ---------------------------------------------------------------------------

@app.route("/api/project/<project_name>/publish", methods=["POST"])
def publish(project_name):
    sanitized = sanitize_name(project_name)
    project_data = load_project(sanitized)
    if project_data is None:
        return jsonify({"error": f"Project '{project_name}' not found"}), 404

    project_dir = utils.PROJECTS_DIR / sanitized
    project_data["status"] = "publishing"
    save_project(sanitized, project_data)

    ok, payload = publish_project(project_data, project_dir)
    if not ok:
        project_data["status"] = "clips_ready"  # roll back status on failure
        save_project(sanitized, project_data)
        return jsonify(payload), 500

    project_data["status"] = "done"
    project_data["iso_path"] = payload["iso_path"]
    save_project(sanitized, project_data)

    return jsonify({"status": "done", "iso_path": payload["iso_path"]}), 200


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------

@app.route("/api/projects", methods=["GET"])
def get_projects():
    projects = list_projects()
    projects.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    for p in projects:
        p["sanitized_name"] = sanitize_name(p["project_name"])
        p["clip_count"] = len([c for c in p.get("clips", []) if not c.get("deleted")])
    return jsonify(projects), 200


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    utils.PROJECTS_DIR.mkdir(exist_ok=True)
    app.run(debug=True, port=5000)
