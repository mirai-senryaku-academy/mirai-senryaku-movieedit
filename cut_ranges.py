#!/usr/bin/env python3
"""出来上がった動画から、指定した区間を削除する（上級：カット編集）。

「1:20から1:35を切って」のような指示を、削除区間として渡す。
残った区間を再エンコードして連結する。

使い方:
  cut_ranges.py <動画> <出力mp4> 1:20-1:35 2:40-2:50 ...
  （時刻は M:SS / 秒 どちらでも。複数指定可）
"""
import subprocess
import sys
from pathlib import Path


def parse_t(s):
    s = s.strip()
    if ":" in s:
        m, sec = s.split(":", 1)
        return int(m) * 60 + float(sec)
    return float(s)


def probe(video):
    import json
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", str(video)],
        capture_output=True, text=True)
    d = json.loads(out.stdout)
    s = d["streams"][0]
    return int(s["width"]), int(s["height"]), float(d["format"]["duration"])


def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(a) < 3:
        sys.exit("使い方: cut_ranges.py <動画> <出力mp4> 1:20-1:35 [2:40-2:50 ...]")
    video, out = Path(a[0]), Path(a[1])
    removes = []
    for r in a[2:]:
        s, e = r.split("-", 1)
        removes.append((parse_t(s), parse_t(e)))
    removes.sort()

    W, H, dur = probe(video)
    # 残す区間 = 全体 - 削除区間
    keep, cur = [], 0.0
    for s, e in removes:
        if s > cur:
            keep.append((cur, min(s, dur)))
        cur = max(cur, e)
    if cur < dur:
        keep.append((cur, dur))
    if not keep:
        sys.exit("全部削除されてしまう。区間を見直して")

    work = video.parent / ".cut"
    work.mkdir(exist_ok=True)
    enc = ["-r", "30", "-s", f"{W}x{H}", "-c:v", "libx264", "-crf", "18",
           "-preset", "medium", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
    pieces = []
    for i, (s, e) in enumerate(keep):
        seg = work / f"k{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s), "-to", str(e),
                        "-i", str(video.resolve()), *enc, str(seg.resolve())], check=True)
        pieces.append(seg)
    lst = work / "c.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in pieces), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", str(out.resolve())], check=True)
    cut_total = sum(e - s for s, e in removes)
    print(f"完成: {out}  （{len(removes)}区間/計{cut_total:.1f}秒を削除）", flush=True)


if __name__ == "__main__":
    main()
