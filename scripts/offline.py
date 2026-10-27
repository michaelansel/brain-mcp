"""Makes a built brain site readable offline.

Called by build-site.sh on the MkDocs output, before it is published. Writes:

- offline.json: every file the site serves, by the path it is fetched at (a
  folder's index.html by the folder), with a hash of its content;
- version.txt: a hash of offline.json, which the worker asks for on every
  page open to learn whether its saved copy is still the current build;
- sw.js: the service worker that saves those files (site/sw.js), the same in
  every build, so the browser reinstalls it only when its code changes — or,
  with BRAIN_SITE_OFFLINE=0, site/sw-off.js, which deletes the saved copy;
- reset/index.html: a page that clears the saved copy by hand.

    offline.py <built site> <repo site dir>
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

SKIP = {".map", ".gz"}  # source maps and pre-compressed copies: never fetched by a reader


def listing(out: Path) -> dict[str, str]:
    files = {}
    for path in sorted(out.rglob("*")):
        if not path.is_file() or path.suffix in SKIP:
            continue
        rel = path.relative_to(out).as_posix()
        if rel.split("/")[0] == "reset" or rel in ("sw.js", "offline.json", "version.txt"):
            continue
        if rel == "index.html" or rel.endswith("/index.html"):
            rel = rel[: -len("index.html")]
        files[rel] = digest(path.read_bytes())
    return files


def digest(data: bytes) -> str:
    """The hash sw.js computes too: the first 16 hex digits of the sha256."""
    return hashlib.sha256(data).hexdigest()[:16]


def main(argv: list[str]) -> None:
    out, site = Path(argv[1]), Path(argv[2])
    manifest = json.dumps({"files": listing(out)}, indent=0, sort_keys=True).encode()
    (out / "offline.json").write_bytes(manifest)
    (out / "version.txt").write_text(digest(manifest) + "\n", encoding="utf-8")
    worker = "sw-off.js" if os.environ.get("BRAIN_SITE_OFFLINE", "1") == "0" else "sw.js"
    (out / "sw.js").write_text((site / worker).read_text(encoding="utf-8"), encoding="utf-8")
    (out / "reset").mkdir(exist_ok=True)
    (out / "reset" / "index.html").write_text(
        (site / "reset.html").read_text(encoding="utf-8"), encoding="utf-8"
    )


if __name__ == "__main__":
    main(sys.argv)
