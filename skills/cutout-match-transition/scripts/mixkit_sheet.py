"""Contact sheet of Mixkit clips straight from their CDN (never touches the rate-limited website).

    mixkit_sheet.py <ids.txt> <work dir>          # ids.txt: "id [slug]" per line, ids from search pages
    -> <work dir>/prev/<id>.mp4 (360p previews), <work dir>/sheet.jpg, and a numbered key on stdout

Mixkit's search/clip pages start answering HTTP 429 after a few dozen requests; the CDN
(assets.mixkit.co) keeps serving. Find ids with a handful of tag searches, then use this.
Full-size download: https://assets.mixkit.co/videos/<id>/<id>-1080.mp4 (fall back to -720 / -360).
"""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ids_file, work = Path(sys.argv[1]), Path(sys.argv[2])
(work / "prev").mkdir(parents=True, exist_ok=True)
(work / "tiles").mkdir(exist_ok=True)
ids = [line.split()[0] for line in ids_file.read_text().splitlines() if line.strip()]


def fetch(cid):
    out = work / "prev" / f"{cid}.mp4"
    if not out.exists():
        subprocess.run(["curl", "-sfL", "--retry", "3", "-A", "Mozilla/5.0", "-o", str(out),
                        f"https://assets.mixkit.co/videos/{cid}/{cid}-360.mp4"])
    return cid, out if out.exists() and out.stat().st_size > 0 else None


with ThreadPoolExecutor(4) as ex:
    previews = dict(ex.map(fetch, ids))

for i, cid in enumerate(ids, 1):
    tile = work / "tiles" / f"{i:03d}.jpg"
    src = previews.get(cid)
    if src:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(src)], capture_output=True, text=True).stdout or 0)
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{dur * 0.4:.2f}", "-i", str(src), "-frames:v", "1"]
        label = f"{dur:.1f}s"
    else:
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=384x216", "-frames:v", "1"]
        label = "no preview"
    subprocess.run(cmd + ["-vf", "scale=384:216:force_original_aspect_ratio=increase,crop=384:216", str(tile)])
    print(f"{i}:{cid} ({label})", end="\n" if i % 6 == 0 else "  ")

subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i", str(work / "tiles" / "%03d.jpg"),
                "-vf", "tile=6x6:padding=6:color=white", "-frames:v", "1", str(work / "sheet.jpg")])
print(f"\n{work / 'sheet.jpg'}")
