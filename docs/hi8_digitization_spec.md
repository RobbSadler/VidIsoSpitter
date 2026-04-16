# Hi-8 Digitization Web App — Full Project Specification

**Project:** Local web app to manage Hi-8 tape digitization workflow  
**Stack:** Python 3, Flask, FFmpeg, dvdauthor, mkisofs  
**Architecture:** Flask serves both the API and the single-page frontend  
**Scope:** 6 self-contained modules, each independently testable

---

## System Dependencies

Install before starting any module:

```bash
# Ubuntu/Debian
sudo apt install ffmpeg dvdauthor mkisofs

# Python packages
pip install flask pillow
```

> **AV Cutty** — download from https://avcutty.sourceforge.net  
> It is invoked as a CLI subprocess. Confirm the binary name after install (likely `avcutty`).

---

## Project Directory Layout

```
hi8app/
├── app.py                  # Flask entry point — all routes defined here
├── modules/
│   ├── splitter.py         # Module 2: video splitting + thumbnails
│   ├── clips.py            # Module 3: clip CRUD operations
│   └── publisher.py        # Module 5: DVD authoring pipeline
├── static/
│   └── app.js              # Module 4: frontend JavaScript
├── templates/
│   └── index.html          # Module 4: single-page HTML/CSS
└── projects/               # Created at runtime, one subfolder per project
    └── {project_name}/
        ├── project.json
        ├── source/         # Symlink or copy of original video
        ├── clips/          # clip_001.mp4, clip_002.mp4 ...
        ├── thumbs/         # clip_001.jpg, clip_002.jpg ...
        ├── deleted/        # Soft-deleted clips moved here
        ├── menu/           # Menu video segments + final loop
        └── output/         # Final dvd/ folder and .iso file
```

---

## project.json Schema

All state is stored here. The app reads this on startup so sessions survive restarts.

```json
{
  "project_id": "uuid-string",
  "project_name": "Summer 1994",
  "source_video": "/absolute/path/to/source.avi",
  "created_at": "2026-04-14T10:00:00Z",
  "status": "clips_ready",
  "clips": [
    {
      "id": "clip_001",
      "filename": "clip_001.mp4",
      "duration_seconds": 47.3,
      "title": "Birthday Cake",
      "deleted": false,
      "order": 1
    }
  ]
}
```

**`status` values:** `created` → `splitting` → `clips_ready` → `publishing` → `done`

---

---

# MODULE 1 — Project Setup

**File:** `app.py` (route) + inline logic (no separate module file needed)

## Purpose
Create the working directory structure and initialize `project.json`.

## Route

```
POST /api/project/create
Content-Type: application/json
```

**Request body:**
```json
{
  "source_video": "/home/user/captures/tape1.avi",
  "project_name": "Summer 1994"
}
```

**Validation:**
- `source_video` must be an absolute path to an existing file
- `project_name` must be non-empty, alphanumeric + spaces only (will be sanitized to underscores for folder name)
- If a project with the same sanitized name already exists, return the existing project (do not overwrite)

**Actions:**
1. Sanitize `project_name` → folder-safe string (e.g. `"Summer 1994"` → `"Summer_1994"`)
2. Create `projects/{sanitized_name}/` and all subdirectories listed above
3. Generate a UUID for `project_id`
4. Write initial `project.json` with `status: "created"` and empty `clips` array
5. Return the project metadata

**Response (200):**
```json
{
  "project_id": "abc-123",
  "project_name": "Summer 1994",
  "working_dir": "/absolute/path/to/projects/Summer_1994",
  "status": "created"
}
```

**Error responses:**
- `400` — missing fields or invalid path
- `409` — project already exists (return existing project data)

## Additional Route

```
GET /api/projects
```
Returns a list of all existing projects (scan `projects/` for valid `project.json` files). Used by the frontend on load to let the user resume a previous project.

---

---

# MODULE 2 — Video Splitting

**File:** `modules/splitter.py`

## Purpose
Split the source video into individual clips using AV Cutty, then generate a thumbnail for each clip using FFmpeg.

## Route

```
POST /api/project/<project_name>/split
```

No request body needed — all info comes from `project.json`.

**Actions (in order):**

### Step 1 — Run AV Cutty

```python
import subprocess

result = subprocess.run(
    ["avcutty", "--input", source_video, "--output-dir", clips_dir],
    capture_output=True, text=True
)
```

> **Note:** AV Cutty's exact CLI flags must be confirmed from its documentation after install. The above is illustrative. If AV Cutty produces clips in a non-mp4 format, add an FFmpeg re-encode step.

**Fallback:** If AV Cutty is unavailable, use FFmpeg scene detection:
```bash
ffmpeg -i source.avi -filter:v "select='gt(scene,0.4)',showinfo" -vsync vfr thumbs/scene_%04d.jpg
```
Then split at detected timestamps. This fallback should be a documented option, not the default.

### Step 2 — Enumerate clips

After AV Cutty runs, scan the `clips/` directory for all output video files. Sort them by filename. Assign IDs: `clip_001`, `clip_002`, etc.

### Step 3 — Generate thumbnails

For each clip, extract one frame at the 2-second mark (or 0.5s if the clip is shorter than 2s):

```bash
ffmpeg -ss 2 -i clips/clip_001.mp4 -vframes 1 -vf scale=320:240 thumbs/clip_001.jpg
```

### Step 4 — Get clip duration

```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 clips/clip_001.mp4
```

### Step 5 — Update project.json

Write all clip metadata into `project.json`. Set `status` to `"clips_ready"`.

**Response (200):**
```json
{
  "status": "clips_ready",
  "clip_count": 12,
  "clips": [ ...clip objects... ]
}
```

**Error responses:**
- `404` — project not found
- `500` — AV Cutty or FFmpeg failed (include stderr in response for debugging)

## Notes
- This operation can take several minutes for long tapes. The route should run synchronously for now (streaming progress is a future enhancement).
- Splitting is non-destructive. The source video is never modified.

---

---

# MODULE 3 — Clip Management

**File:** `modules/clips.py`

## Purpose
CRUD operations on individual clips. All changes persist to `project.json`.

---

## Route: List Clips

```
GET /api/project/<project_name>/clips
```

Returns all non-deleted clips in order.

**Response:**
```json
{
  "clips": [
    {
      "id": "clip_001",
      "filename": "clip_001.mp4",
      "duration_seconds": 47.3,
      "title": "Birthday Cake",
      "thumbnail_url": "/api/project/Summer_1994/thumbs/clip_001.jpg",
      "stream_url": "/api/project/Summer_1994/stream/clip_001",
      "order": 1
    }
  ]
}
```

---

## Route: Update Clip Title

```
PATCH /api/project/<project_name>/clips/<clip_id>
Content-Type: application/json
```

**Request body:**
```json
{ "title": "Birthday Cake" }
```

- Finds the clip by `id` in `project.json`
- Updates `title` field
- Writes updated `project.json`

**Response:** Updated clip object (same shape as above)  
**Errors:** `404` if clip not found, `400` if title is empty

---

## Route: Reorder Clips

```
POST /api/project/<project_name>/clips/reorder
Content-Type: application/json
```

**Request body:**
```json
{ "order": ["clip_003", "clip_001", "clip_002"] }
```

Updates the `order` field on each clip. Used if the user drags to reorder (frontend enhancement — implement if time allows).

---

## Route: Delete Clip (soft delete)

```
DELETE /api/project/<project_name>/clips/<clip_id>
```

- Moves `clips/clip_001.mp4` → `deleted/clip_001.mp4`
- Moves `thumbs/clip_001.jpg` → `deleted/clip_001.jpg`
- Sets `"deleted": true` on the clip in `project.json`
- Does NOT remove the entry from the clips array (preserves history)

**Response:** `{ "deleted": true, "clip_id": "clip_001" }`

---

## Route: Serve Thumbnail

```
GET /api/project/<project_name>/thumbs/<filename>
```

Serves the JPEG thumbnail file from the `thumbs/` directory using Flask's `send_file()`.

---

## Route: Stream Clip Video

```
GET /api/project/<project_name>/stream/<clip_id>
```

Streams the clip's mp4 file using Flask's `send_file()` with `conditional=True` to support HTTP range requests (required for browser `<video>` seek to work correctly).

```python
from flask import send_file, request

@app.route('/api/project/<project_name>/stream/<clip_id>')
def stream_clip(project_name, clip_id):
    clip_path = f"projects/{project_name}/clips/{clip_id}.mp4"
    return send_file(clip_path, mimetype='video/mp4', conditional=True)
```

---

---

# MODULE 4 — Frontend UI

**Files:** `templates/index.html`, `static/app.js`

## Purpose
Single-page browser interface. No framework — plain HTML, CSS, and vanilla JavaScript.

---

## Page Structure

```
┌─────────────────────────────────────────────────────────┐
│  🎬 Hi-8 Digitizer                                      │
│  ┌──────────────────────────────────────────────────┐   │
│  │ Project Name: [_______________]                  │   │
│  │ Source Video: [_______________] [Browse]         │   │
│  │ [Load / Create Project]                          │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  Project: Summer 1994    12 clips    [▶ Split Video]    │
│  ─────────────────────────────────────────────────────  │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │  [thumb] │  │  [thumb] │  │  [thumb] │              │
│  │  ▶ Play  │  │  ▶ Play  │  │  ▶ Play  │              │
│  └──────────┘  └──────────┘  └──────────┘              │
│  🗑 [Title____]  🗑 [Title____]  🗑 [Title____]         │
│                                                         │
│  ┌──────────┐  ┌──────────┐                            │
│  │  [thumb] │  │  [thumb] │                            │
│  │  ▶ Play  │  │  ▶ Play  │                            │
│  └──────────┘  └──────────┘                            │
│  🗑 [Title____]  🗑 [Title____]                         │
│                                                         │
│                              [🔴 Publish DVD]           │
└─────────────────────────────────────────────────────────┘
```

---

## Setup Panel (shown when no project is loaded)

- Text input: Project Name
- Text input: Source Video (full file path)
- Button: "Load / Create Project" → calls `POST /api/project/create`, then `GET /api/project/<name>/clips`
- If project exists already (409 response), load existing clips directly

---

## Clip Grid

- CSS Grid layout, `auto-fill`, columns sized to `320px`, gap `16px`
- Each card contains:

### Thumbnail / Video Toggle
- `<img>` tag showing the thumbnail (320×240)
- Overlaid play button (▶) centered on the image
- On click:
  - Hide the `<img>`, show a `<video>` element with `src` set to the stream URL
  - Call `video.play()`
  - Change overlay icon to ■ (stop)
  - On second click: call `video.pause()`, hide `<video>`, show `<img>` again
  - Only one clip plays at a time — clicking a new clip stops any currently playing one

### Title Input
- `<input type="text">` below the thumbnail
- Placeholder: `"Clip N"` (auto-numbered)
- On `blur` or `Enter` keypress: fire `PATCH` request to save title
- Show a brief "✓ Saved" confirmation next to the field (fade out after 1.5s)

### Delete Button
- Trashcan icon (🗑 or SVG) next to the title input
- On click: show a simple `confirm()` dialog — "Delete this clip? It can be recovered manually."
- On confirm: call `DELETE` endpoint, fade-and-remove the card from the DOM

---

## Publish Button

- Fixed to bottom-right of page (or below the grid)
- Disabled (greyed out) until at least one clip has a non-empty title
- Label: "Publish DVD"
- On click: calls `POST /api/project/<name>/publish`
- While publishing: disable button, show a status panel with a scrolling log (see Module 5)

---

## Status / Log Panel

- Hidden by default, shown during splitting and publishing
- A `<pre>` or `<div>` that receives progress messages via polling or SSE
- Messages like:
  - "Running AV Cutty..."
  - "Generating thumbnails... (4/12)"
  - "Building menu video..."
  - "Running dvdauthor..."
  - "Creating ISO..."
  - "✅ Done! Output: projects/Summer_1994/output/Summer_1994.iso"

---

## JavaScript API Wrapper (in app.js)

Define simple async functions to keep the code readable:

```javascript
async function createProject(name, videoPath) { ... }
async function getClips(projectName) { ... }
async function updateTitle(projectName, clipId, title) { ... }
async function deleteClip(projectName, clipId) { ... }
async function publishDVD(projectName) { ... }
```

All functions use `fetch()`. All errors are caught and displayed in a visible error banner at the top of the page.

---

---

# MODULE 5 — DVD Publishing Pipeline

**File:** `modules/publisher.py`

## Purpose
Build the DVD menu video, author the DVD structure, and create the final ISO file.

## Route

```
POST /api/project/<project_name>/publish
```

Runs the full pipeline synchronously (long-running). Returns final status when complete.

> **Future enhancement:** Convert to SSE streaming so the frontend can show live progress. For the initial build, polling a status endpoint every 2 seconds is acceptable.

---

## Pipeline Steps

### Step 1 — Extract menu segments

For each non-deleted clip (in order), extract the first 3 seconds:

```bash
ffmpeg -i clips/clip_001.mp4 -t 3 -vf scale=720:480 -r 29.97 menu/seg_001.mp4
```

### Step 2 — Concatenate menu segments

Create a concat list file:
```
# menu/concat.txt
file 'seg_001.mp4'
file 'seg_002.mp4'
...
```

Concatenate into a single looping menu background video:
```bash
ffmpeg -f concat -safe 0 -i menu/concat.txt -c copy menu/menu_bg.mp4
```

### Step 3 — Generate menu title overlay image

Use Python's `Pillow` library to draw a PNG image (720×480) with:
- Project name as the title at the top
- Each clip title listed as a numbered menu item
- Dark semi-transparent background for readability

```python
from PIL import Image, ImageDraw, ImageFont

img = Image.new("RGBA", (720, 480), (0, 0, 0, 180))
draw = ImageDraw.Draw(img)
draw.text((40, 30), project_name, fill="white")
for i, clip in enumerate(clips):
    draw.text((60, 100 + i * 40), f"{i+1}. {clip['title']}", fill="white")
img.save("menu/menu_overlay.png")
```

### Step 4 — Compose menu video with overlay

Burn the overlay into the menu background:
```bash
ffmpeg -i menu/menu_bg.mp4 -i menu/menu_overlay.png \
  -filter_complex "overlay=0:0" -c:v mpeg2video -b:v 4000k \
  -c:a ac3 -ar 48000 menu/menu_final.mpg
```

> **Note:** dvdauthor requires MPEG-2 video and AC-3 audio. The `-c:v mpeg2video` flag handles this. If source clips are not DVD-compliant, they also need re-encoding in this step.

### Step 5 — Re-encode clips to DVD format (if needed)

```bash
ffmpeg -i clips/clip_001.mp4 \
  -vf scale=720:480 -r 29.97 \
  -c:v mpeg2video -b:v 5000k \
  -c:a ac3 -ar 48000 \
  output/dvd_clips/clip_001.mpg
```

Check if clips are already MPEG-2 before re-encoding to save time.

### Step 6 — Generate dvdauthor XML

```python
def generate_dvdauthor_xml(project_name, clips, output_dir):
    vobs = "\n".join(
        f'        <vob file="{output_dir}/dvd_clips/{c["id"]}.mpg" chapters="0"/>'
        for c in clips
    )
    xml = f"""<dvdauthor dest="{output_dir}/dvd">
  <vmgm>
    <menus>
      <pgc entry="title">
        <vob file="{output_dir}/menu/menu_final.mpg" pause="inf"/>
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
    return xml
```

Write this to `output/dvd.xml`.

### Step 7 — Run dvdauthor

```bash
export VIDEO_FORMAT=NTSC
dvdauthor -o output/dvd -x output/dvd.xml
dvdauthor -T -o output/dvd
```

### Step 8 — Create ISO

```bash
mkisofs -dvd-video -V "Summer_1994" -o output/Summer_1994.iso output/dvd
```

> Volume label (`-V`) max 32 characters, uppercase, no spaces — sanitize `project_name` accordingly.

### Step 9 — Update project.json

Set `status: "done"`. Store the ISO path.

**Response:**
```json
{
  "status": "done",
  "iso_path": "/absolute/path/to/projects/Summer_1994/output/Summer_1994.iso"
}
```

**Error responses:**
- `500` with `{ "error": "...", "step": "dvdauthor", "stderr": "..." }` — include which step failed and its stderr output so the user can diagnose

---

---

# MODULE 6 — State Persistence & Project Resume

**Handled in:** `app.py` and `modules/clips.py`

## Purpose
The app must be closeable and reopenable without losing work.

## Implementation

All state lives in `project.json`. No database required.

### Helper functions (put in a shared `utils.py`):

```python
import json, os

PROJECTS_DIR = os.path.join(os.path.dirname(__file__), "projects")

def load_project(project_name):
    path = os.path.join(PROJECTS_DIR, project_name, "project.json")
    with open(path) as f:
        return json.load(f)

def save_project(project_name, data):
    path = os.path.join(PROJECTS_DIR, project_name, "project.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def list_projects():
    projects = []
    for name in os.listdir(PROJECTS_DIR):
        json_path = os.path.join(PROJECTS_DIR, name, "project.json")
        if os.path.exists(json_path):
            projects.append(load_project(name))
    return projects
```

### On app startup

Flask serves `GET /` which loads `index.html`. The frontend JS immediately calls `GET /api/projects` to check for existing projects and shows them as resumable options.

---

---

# BUILD ORDER

Build and test one module at a time in this sequence:

| Step | Module | What to test |
|------|--------|-------------|
| 1 | Flask skeleton (`app.py`) | All routes return stub JSON. Frontend loads. |
| 2 | Module 1 — Project Setup | Directory created, `project.json` written, resume works. |
| 3 | Module 2 — Splitting | Clips appear in `clips/`, thumbnails in `thumbs/`. |
| 4 | Module 4 — Frontend UI | Grid renders, thumbnails show, play/stop works, titles save. |
| 5 | Module 3 — Clip Management | Delete fades card, title PATCH persists across reload. |
| 6 | Module 5 — Publishing | ISO created, opens in VLC or burns correctly. |

---

# PROMPTS FOR EACH MODULE

Use these prompts verbatim when starting a new AI session. Always paste the relevant module spec section along with the prompt.

---

**Module 1 prompt:**
> "I am building a local Flask web app for Hi-8 video digitization. Please implement Module 1 — Project Setup — exactly as described in the attached spec. Use Python 3 and Flask. Create `app.py` with the `/api/project/create` and `/api/projects` routes, plus a `utils.py` with the load/save/list helper functions. Follow the project directory layout in the spec."

---

**Module 2 prompt:**
> "I have a working Flask app (`app.py`) for a Hi-8 digitization tool. Please implement Module 2 — Video Splitting — as described in the spec. Create `modules/splitter.py` and add the `POST /api/project/<project_name>/split` route to `app.py`. Use subprocess to call AV Cutty, then FFmpeg for thumbnails. Here is my current `app.py`: [paste file]"

---

**Module 3 prompt:**
> "I have a working Flask app for Hi-8 digitization with project setup and splitting working. Please implement Module 3 — Clip Management — as described in the spec. Create `modules/clips.py` and add all clip routes to `app.py`. Here is my current `app.py` and `modules/splitter.py`: [paste files]"

---

**Module 4 prompt:**
> "I have a working Flask backend for Hi-8 digitization (project setup, splitting, and clip management all work). Please implement Module 4 — the Frontend UI — as described in the spec. Create `templates/index.html` and `static/app.js`. Use plain HTML, CSS, and vanilla JavaScript — no frameworks. The backend API routes are: [list routes from your working app.py]"

---

**Module 5 prompt:**
> "I have a working Flask app and frontend for Hi-8 digitization. Please implement Module 5 — DVD Publishing — as described in the spec. Create `modules/publisher.py` and add the `POST /api/project/<project_name>/publish` route to `app.py`. Use subprocess for FFmpeg, dvdauthor, and mkisofs. Use Pillow for the menu overlay image. Here is my current `app.py`: [paste file]"

---

*End of specification.*
