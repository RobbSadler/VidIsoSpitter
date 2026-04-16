import json
import os
import re
from pathlib import Path

# Default location — overridden at startup if settings.json has a projects_dir
PROJECTS_DIR = Path(__file__).parent / "projects"

SETTINGS_FILE = Path(__file__).parent / "settings.json"


def load_settings() -> dict:
    if SETTINGS_FILE.is_file():
        try:
            with SETTINGS_FILE.open() as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_settings(data: dict) -> None:
    with SETTINGS_FILE.open("w") as f:
        json.dump(data, f, indent=2)


def sanitize_name(name: str) -> str:
    """Replace whitespace with underscores; collapse multiple underscores."""
    return re.sub(r"_+", "_", name.strip().replace(" ", "_"))


def load_project(sanitized_name: str) -> dict | None:
    """Load project.json for the given sanitized project folder name.
    Returns the parsed dict, or None if not found / invalid."""
    project_file = PROJECTS_DIR / sanitized_name / "project.json"
    if not project_file.is_file():
        return None
    try:
        with project_file.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_project(sanitized_name: str, data: dict) -> None:
    """Write project.json for the given sanitized project folder name."""
    project_file = PROJECTS_DIR / sanitized_name / "project.json"
    with project_file.open("w") as f:
        json.dump(data, f, indent=2)


def list_projects() -> list[dict]:
    """Scan the projects/ directory and return all valid project records."""
    projects = []
    if not PROJECTS_DIR.is_dir():
        return projects
    for entry in sorted(PROJECTS_DIR.iterdir()):
        if entry.is_dir():
            data = load_project(entry.name)
            if data is not None:
                projects.append(data)
    return projects
