"""Runtime paths shared by the Windows server, GUI, and tray app."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping


APP_DIR = Path(__file__).resolve().parent
APP_DATA_DIR_NAME = "Remote Voice"


def config_path(
    filename: str,
    *,
    app_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return a writable config path, seeding packaged installs on first use.

    Source checkouts keep using the JSON files beside the scripts. Packaged
    installs are identified by their ``defaults`` directory and store mutable
    files under the current user's local application-data directory.
    """
    app_dir = Path(app_dir or APP_DIR)
    defaults_dir = app_dir / "defaults"
    if not defaults_dir.is_dir():
        return app_dir / filename

    env = os.environ if environ is None else environ
    local_app_data = env.get("LOCALAPPDATA")
    if local_app_data:
        data_dir = Path(local_app_data) / APP_DATA_DIR_NAME
    else:
        data_dir = Path.home() / "AppData" / "Local" / APP_DATA_DIR_NAME

    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / filename
    if target.exists():
        return target

    # Prefer a config left by the earlier installer layout, then fall back to
    # the clean packaged default. copy2 is intentionally non-overwriting here:
    # an existing per-user file always wins.
    for source in (app_dir / filename, defaults_dir / filename):
        if source.is_file():
            shutil.copy2(source, target)
            break
    return target
