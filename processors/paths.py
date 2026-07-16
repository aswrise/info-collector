from pathlib import Path
import re


def collection_path(root: str | Path, name: str) -> Path:
    safe = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", name).strip(" .")[:120]
    if not safe:
        raise ValueError("collection folder name is empty")
    return Path(root).expanduser() / safe
