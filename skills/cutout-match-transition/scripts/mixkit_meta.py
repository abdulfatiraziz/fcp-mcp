"""Fetch Mixkit clip metadata (thumbnail, file URLs, duration) and thumbnails.

mixkit_meta.py <candidates.txt with "id slug" lines> <thumbs dir>   -> JSON list on stdout
Failures are reported per clip ("error") instead of aborting the batch.
"""
import json, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

out_dir = Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)


def get(url):
    return subprocess.run(["curl", "-sfL", "--retry", "4", "--retry-all-errors", "--retry-delay", "1",
                           "-A", "Mozilla/5.0", "--max-time", "60", url], capture_output=True, check=True).stdout


def fetch(line):
    cid, slug = line.split()
    url = f"https://mixkit.co/free-stock-video/{slug}-{cid}/"
    meta = {"id": cid, "slug": slug, "page": url}
    try:
        html = get(url).decode("utf-8", "replace")
        grab = lambda key: (m.group(1) if (m := re.search(rf'"{key}":"([^"]+)"', html)) else None)  # noqa: E731
        files = re.findall(rf"https://assets\.mixkit\.co/(?:videos/{cid}/{cid}|active_storage/video_items/{cid}/\d+/{cid}-video)-(\d+)\.mp4", html)
        meta.update(thumb=grab("thumbnailUrl"), duration=grab("duration"), files=sorted(set(files), key=int))
        if meta["thumb"]:
            (out_dir / f"{cid}.jpg").write_bytes(get(meta["thumb"]))
    except subprocess.CalledProcessError as e:
        meta["error"] = f"curl exit {e.returncode}"
    return meta


lines = [l.strip() for l in Path(sys.argv[1]).read_text().splitlines() if l.strip()]
with ThreadPoolExecutor(6) as ex:
    print(json.dumps(list(ex.map(fetch, lines)), indent=1))
