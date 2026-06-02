#!/usr/bin/env python3
"""libass非搭載のffmpegでもテロップを焼き込む。

各テロップを Pillow で透過PNG化し、ffmpeg の overlay フィルタで時間指定合成する。
白文字＋黒縁＋半透明の帯（60代向けに可読性重視）。subtitles/drawtext 不要。

使い方:
  .venv/bin/python burn_overlay.py "<動画>" "<SRT>" [出力mp4]
"""
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = str(Path(__file__).resolve().parent / "fonts" / "NotoSansJP-VF.ttf")
FONT_WEIGHT = "Black"
ACCENT = (255, 222, 0, 255)   # 強調キーワードの色（黄）
WHITE = (255, 255, 255, 255)


def load_keywords(terms_path: Path):
    """用語辞書の「正しい表記」側を強調キーワードにする＋数字パターン。"""
    kws = {"軽擦", "手技", "施術", "血行", "揉み方", "大別", "リラクゼーション",
           "緊張", "効果", "マッサージ"}
    if terms_path.exists():
        for line in terms_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sep = "\t" if "\t" in line else ("=" if "=" in line else None)
            if sep:
                right = line.split(sep, 1)[1].strip()
                # 文脈付きルール（例: 手技を大別して）は最後の語だけ拾わず全体を弾く
                if right and len(right) <= 8 and "して" not in right:
                    kws.add(right)
    parts = [re.escape(k) for k in sorted(kws, key=len, reverse=True) if k]
    parts.append(r"\d+(?:番目|つ|本|回|秒|分|％|%)?")
    parts.append(r"[一二三四五六七八九十百]+(?:番目|つ|回|本)")
    return re.compile("|".join(parts))


def segment_line(line: str, kre):
    """行を (テキスト, 強調か) のスパン列に分ける。"""
    spans, pos = [], 0
    for m in kre.finditer(line):
        if m.start() > pos:
            spans.append((line[pos:m.start()], False))
        spans.append((m.group(), True))
        pos = m.end()
    if pos < len(line):
        spans.append((line[pos:], False))
    return spans or [(line, False)]


def probe_size(video: Path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(video)],
        capture_output=True, text=True)
    d = json.loads(out.stdout)["streams"][0]
    return int(d["width"]), int(d["height"])


def parse_srt(path: Path):
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip())
    caps = []
    for b in blocks:
        lines = b.strip().splitlines()
        if len(lines) < 2:
            continue
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        g = list(map(int, m.groups()))
        start = g[0]*3600 + g[1]*60 + g[2] + g[3]/1000
        end = g[4]*3600 + g[5]*60 + g[6] + g[7]/1000
        text = "\n".join(lines[2:])
        caps.append((start, end, text))
    return caps


def render_png(text: str, W: int, H: int, font: ImageFont.FreeTypeFont, kre, out: Path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lines = text.split("\n")
    asc, desc = font.getmetrics()
    lh = asc + desc + int(font.size * 0.30)
    block_h = lh * len(lines)
    margin_v = int(H * 0.05)
    y0 = H - margin_v - block_h
    stroke = max(3, int(font.size * 0.14))
    line_w = [d.textlength(ln, font=font) for ln in lines]

    for i, ln in enumerate(lines):
        y = y0 + i * lh
        x = (W - line_w[i]) / 2
        for span, is_key in segment_line(ln, kre):
            color = ACCENT if is_key else WHITE
            d.text((x, y), span, font=font, fill=color,
                   stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
            x += d.textlength(span, font=font)
    img.save(out)


def main():
    if len(sys.argv) < 3:
        sys.exit("使い方: burn_overlay.py <動画> <SRT> [出力mp4]")
    video = Path(sys.argv[1])
    srt = Path(sys.argv[2])
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else video.with_name(video.stem + "_telop.mp4")

    W, H = probe_size(video)
    font_size = max(24, int(H * 0.072))
    font = ImageFont.truetype(FONT_PATH, font_size)
    try:
        font.set_variation_by_name(FONT_WEIGHT)
    except Exception:
        pass
    kre = load_keywords(Path(__file__).resolve().parent / "用語辞書.txt")
    caps = parse_srt(srt)
    print(f"動画 {W}x{H} / テロップ {len(caps)}枚 / フォント{font_size}px / 強調ON", flush=True)

    tmp = video.parent / ".telop_png"
    tmp.mkdir(exist_ok=True)
    for i, (s, e, text) in enumerate(caps):
        render_png(text, W, H, font, kre, tmp / f"c{i:04d}.png")

    # filter_complex を組む（PNGを時間指定でoverlay）
    inputs = ["-i", str(video.resolve())]
    for i in range(len(caps)):
        inputs += ["-i", str((tmp / f"c{i:04d}.png").resolve())]
    parts, prev = [], "0:v"
    for i, (s, e, _t) in enumerate(caps):
        lbl = f"v{i}"
        parts.append(
            f"[{prev}][{i+1}:v]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'[{lbl}]")
        prev = lbl
    fc = ";".join(parts)
    fc_file = tmp / "filter.txt"
    fc_file.write_text(fc, encoding="utf-8")

    cmd = ["ffmpeg", "-y", *inputs,
           "-filter_complex_script", str(fc_file.resolve()),
           "-map", f"[{prev}]", "-map", "0:a?",
           "-c:v", "h264_videotoolbox", "-b:v", "3M",
           "-c:a", "copy", str(out.resolve())]
    print("焼き込み中（ffmpeg overlay）...", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("ffmpeg失敗:\n" + r.stderr[-1200:])
    print(f"完成: {out}", flush=True)


if __name__ == "__main__":
    main()
