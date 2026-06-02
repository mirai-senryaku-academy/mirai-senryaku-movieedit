#!/usr/bin/env python3
"""完成動画にBGMを乗せる。小音量＋自動ダッキング＋ループ＋終端フェードアウト。

- BGMは動画尺へループ（長ければ自動カット）
- ナレーション(元音声)に反応してBGMを自動で下げる（sidechaincompress＝ダッキング）
- 出だしフェードイン／終わりフェードアウト
- 映像は再エンコードしない（-c:v copy）ので速い

使い方: add_bgm.py <動画> <BGM(mp3/ファイル or bgm/の名前)> [出力mp4] [--vol 0.28]
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def probe_dur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


def resolve_bgm(name):
    p = Path(name)
    if p.exists():
        return p
    # bgm/ フォルダ内を名前で探す（部分一致可）
    cands = sorted((HERE / "bgm").glob("*.mp3"))
    for c in cands:
        if name in c.stem:
            return c
    if name in ("auto", "") and cands:
        return cands[0]
    sys.exit(f"BGMが見つからない: {name}（bgm/ にあるか確認）")


def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    flags = [x for x in sys.argv[1:] if x.startswith("--")]
    if len(a) < 2:
        sys.exit("使い方: add_bgm.py <動画> <BGM> [出力mp4] [--vol 0.28]")
    video = Path(a[0])
    bgm = resolve_bgm(a[1])
    out = Path(a[2]) if len(a) > 2 else video.with_name(video.stem + "_bgm.mp4")
    vol = 0.28
    for f in flags:
        if f.startswith("--vol"):
            vol = float(f.split("=")[-1])

    dur = probe_dur(video)
    fade_st = max(0.0, dur - 2.0)
    fc = (
        f"[1:a]volume={vol},afade=t=in:st=0:d=1.0[bgmv];"
        f"[0:a]asplit=2[v1][v2];"
        f"[bgmv][v2]sidechaincompress=threshold=0.04:ratio=8:attack=20:release=400[duck];"
        f"[v1][duck]amix=inputs=2:normalize=0:duration=first[mx];"
        f"[mx]afade=t=out:st={fade_st:.2f}:d=2[aout]"
    )
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-i", str(video.resolve()),
           "-stream_loop", "-1", "-i", str(bgm.resolve()),
           "-filter_complex", fc,
           "-map", "0:v", "-map", "[aout]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
           str(out.resolve())]
    print(f"BGM={bgm.name} vol={vol} ダッキングON ループ＋終端フェード / 動画{dur:.0f}s", flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit("BGMミックス失敗")
    print(f"完成: {out}", flush=True)


if __name__ == "__main__":
    main()
