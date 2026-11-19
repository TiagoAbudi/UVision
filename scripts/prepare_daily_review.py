"""Build the 2026-10-29 review snapshot from user-confirmed normal recordings.

This snapshot follows the user's explicit confirmation that no tests occurred
after the 2026-10-28 00:15 clip. It must not be used to label future sessions.
"""
from __future__ import annotations

import json
from pathlib import Path

from camera_ai.web_dashboard import PROJECT_DIR


def main() -> None:
    root = PROJECT_DIR / "datasets" / "ch04-calibration"
    previous = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    entries = {item["file"]: item for item in previous["clips"]}
    for path in sorted((root / "reference-videos").glob("queda-ch*.mp4")):
        if path.name in entries:
            continue
        if not path.name.startswith(("queda-ch04_2026-10-28_", "queda-ch04_2026-10-29_")):
            raise ValueError(f"Recording outside the reviewed session: {path.name}")
        positive = path.name == "queda-ch04_2026-10-28_00-15-04.mp4"
        entries[path.name] = {
            "file": path.name,
            "fall": positive,
            "activity": "User-confirmed controlled fall" if positive else "User-confirmed normal activity; daily review snapshot",
        }
    result = {
        "channel": 4,
        "label_source": "User explicitly confirmed on 2026-10-29 that, apart from the 00:15 test, all new recordings were normal activities. Earlier nine labels retained.",
        "scope": "Clip-level review snapshot; labels must not be extrapolated to future recordings.",
        "clips": sorted(entries.values(), key=lambda item: item["file"]),
    }
    destination = root / "review-2026-10-29.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"clips": len(entries), "falls": sum(item["fall"] for item in entries.values()), "manifest": str(destination)}))


if __name__ == "__main__":
    main()
