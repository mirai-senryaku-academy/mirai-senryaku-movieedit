#!/usr/bin/env python3
"""扉絵（章タイトル画面）を生成して、テロップ済み動画に挟み込む。

各章の頭フレームをぼかし＋暗くした背景に見出しを乗せて扉絵クリップ(既定2秒・無音)を作り、
テロップ済み動画を章境界で分割→扉絵を挟んで連結する（コーデックを揃えてlibx264で統一）。

章立ては chapters: [(time_or_None, heading, bg_image_or_None, full_image_or_None), ...]
time=None は冒頭オープニング扉絵。
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_PATH = str(Path(__file__).resolve().parent / "fonts" / "NotoSansJP-VF.ttf")
CARD_DUR = 2.0
FPS = 30


def font(size, weight="Black"):
    f = ImageFont.truetype(FONT_PATH, size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def probe_size(video):
    import json
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", str(video)],
        capture_output=True, text=True)
    s = json.loads(out.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


def grab_frame(video, t, W, H, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(max(0, t)),
                    "-i", str(video), "-frames:v", "1", "-vf", f"scale={W}:{H}",
                    str(out)], check=True)


def make_card_png(video, t, heading, W, H, out, bg_image=None, full_image=None):
    if full_image:                       # 自作扉絵をそのまま使う
        Image.open(full_image).convert("RGB").resize((W, H)).save(out)
        return
    if bg_image:                         # 指定画像を背景に
        bg = Image.open(bg_image).convert("RGB").resize((W, H))
    else:                                # 章頭フレームをぼかして背景
        tmp = out.with_suffix(".src.png")
        grab_frame(video, t, W, H, tmp)
        bg = Image.open(tmp).convert("RGB")
        tmp.unlink(missing_ok=True)
    bg = bg.filter(ImageFilter.GaussianBlur(W * 0.012))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    bg = Image.blend(bg, dark, 0.45)     # 文字が乗るよう暗くする
    d = ImageDraw.Draw(bg)
    fz = int(H * 0.12)
    fnt = font(fz)
    stroke = max(3, int(fz * 0.08))
    # 複数行対応（\nで分ける）
    lines = heading.split("\n")
    lh = fz + int(fz * 0.3)
    y0 = (H - lh * len(lines)) // 2
    for i, ln in enumerate(lines):
        w = d.textlength(ln, font=fnt)
        d.text(((W - w) / 2, y0 + i * lh), ln, font=fnt, fill=(255, 255, 255),
               stroke_width=stroke, stroke_fill=(0, 0, 0))
    bg.save(out)


def render_side_label(text, W, H, out):
    """右上の常駐ラベル。3倍解像度で描いて縮小しアンチエイリアス。
    モダンな浮いたカード風：柔らかい大きめの影＋ほぼ白の角丸カード＋黒文字。重い枠は付けない。"""
    ss = 3                                   # スーパーサンプリング倍率（ギザつき防止）
    fz = int(H * 0.05) * ss
    fnt = font(fz, "Bold")
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    tw = probe.textlength(text, font=fnt)
    asc, desc = fnt.getmetrics()
    th = asc + desc
    padx, pady = int(fz * 0.8), int(fz * 0.5)
    bw, bh = int(tw + padx * 2), int(th + pady * 2)
    rad = 0                                  # 角は丸めない（長方形）
    sm = int(fz * 1.1)                       # 影ぶんの余白
    TW, TH = bw + sm * 2, bh + sm * 2
    px, py = sm, sm

    tile = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
    # 柔らかい大きめのドロップシャドウ（浮遊感＝モダン）
    sh = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [px, py + int(fz * 0.22), px + bw, py + bh + int(fz * 0.22)],
        radius=rad, fill=(0, 0, 0, 130))
    tile = Image.alpha_composite(tile, sh.filter(ImageFilter.GaussianBlur(int(fz * 0.5))))
    # ほぼ白の角丸カード（ごく薄い縦グラデ）
    grad = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    gp = grad.load()
    for yy in range(bh):
        v = int(255 - 10 * (yy / max(1, bh - 1)))
        for xx in range(bw):
            gp[xx, yy] = (v, v, v, 255)
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bw - 1, bh - 1], radius=rad, fill=255)
    tile.paste(grad, (px, py), mask)
    # 縁取り文字（白文字＋黒フチ）
    ImageDraw.Draw(tile).text((px + padx, py + pady - int(fz * 0.05)), text, font=fnt,
                              fill=(255, 255, 255, 255),
                              stroke_width=max(3, int(fz * 0.10)), stroke_fill=(0, 0, 0, 255))
    # 縮小してアンチエイリアス
    tile = tile.resize((TW // ss, TH // ss), Image.LANCZOS)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    margin = int(H * 0.028)
    x = (W - margin) - (sm + bw) // ss
    y = margin - sm // ss
    img.alpha_composite(tile, (int(x), int(y)))
    img.save(out)


def enc_common(W, H):
    return ["-r", str(FPS), "-s", f"{W}x{H}", "-c:v", "libx264", "-crf", "18",
            "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]


def card_clip(png, W, H, out, dur=CARD_DUR):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-loop", "1", "-t", str(dur), "-i", str(png),
                    "-f", "lavfi", "-t", str(dur), "-i", "anullsrc=r=48000:cl=stereo",
                    *enc_common(W, H), "-shortest", str(out)], check=True)


def video_segment(video, s, e, W, H, out, label_png=None):
    args = ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s)]
    if e is not None:
        args += ["-to", str(e)]
    args += ["-i", str(video)]
    if label_png:   # 右上の常駐ラベルを重ねる
        args += ["-i", str(label_png),
                 "-filter_complex", "[0:v][1:v]overlay=0:0[v]", "-map", "[v]", "-map", "0:a?"]
    args += [*enc_common(W, H), str(out)]
    subprocess.run(args, check=True)


def probe_dur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    return float(out.stdout.strip())


def assemble_xfade(pieces, out, dur, trans):
    """各ピースをクロスフェードで連結（バツッと切り替わらない）。"""
    durs = [probe_dur(p) for p in pieces]
    inputs = []
    for p in pieces:
        inputs += ["-i", str(p.resolve())]
    vfil, afil = [], []
    vlab, alab = "0:v", "0:a"
    offset = durs[0]
    for i in range(1, len(pieces)):
        nv, na = f"vx{i}", f"ax{i}"
        off = offset - dur
        vfil.append(f"[{vlab}][{i}:v]xfade=transition={trans}:duration={dur}:offset={off:.3f}[{nv}]")
        afil.append(f"[{alab}][{i}:a]acrossfade=d={dur}[{na}]")
        vlab, alab = nv, na
        offset = offset + durs[i] - dur
    fc = ";".join(vfil + afil)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-filter_complex", fc, "-map", f"[{vlab}]", "-map", f"[{alab}]",
           "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", str(out.resolve())]
    subprocess.run(cmd, check=True)


def parse_time(s):
    s = s.strip().strip("[]")
    if s.upper() == "OP":
        return None
    if ":" in s:
        m, sec = s.split(":", 1)
        return int(m) * 60 + float(sec)
    return float(s)


def parse_chapters_file(path):
    """章立て.txt を読む。各行: `時刻<空白>見出し [img=... / full=...]`。[OP]行は冒頭扉絵。"""
    chapters = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        t = parse_time(parts[0])
        rest = parts[1]
        img = full = None
        toks = rest.split()
        kept = []
        for tk in toks:
            if tk.startswith("img="):
                img = tk[4:]
            elif tk.startswith("full="):
                full = tk[5:]
            else:
                kept.append(tk)
        chapters.append((t, " ".join(kept), img, full))
    return chapters


def side_only(video, secs, out, W, H, work):
    """扉絵を入れず、各章の間だけ右上ラベルを常駐させる（1パス overlay）。"""
    inputs = ["-i", str(video.resolve())]
    parts, prev_lab = [], "0:v"
    times = [(t, h) for (t, h, *_ ) in secs]
    n = 0
    for i, (t, h) in enumerate(times):
        e = times[i + 1][0] if i + 1 < len(times) else 1e9
        png = work / f"slabel{i}.png"
        render_side_label(h, W, H, png)
        inputs += ["-i", str(png.resolve())]
        n += 1
        lab = f"s{i}"
        parts.append(f"[{prev_lab}][{n}:v]overlay=0:0:enable='between(t,{t:.3f},{e:.3f})'[{lab}]")
        prev_lab = lab
    fc = ";".join(parts)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-filter_complex", fc, "-map", f"[{prev_lab}]", "-map", "0:a?",
           "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", str(out.resolve())]
    subprocess.run(cmd, check=True)


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    flags = [x for x in sys.argv[1:] if x.startswith("--")]
    if len(args) < 2:
        sys.exit("使い方: titlecard.py <動画> <出力mp4> --chapters=章立て.txt [--cards] [--side] [--xfade=0.45]")
    video = Path(args[0])
    out = Path(args[1])
    xfade, trans = 0.45, "fade"
    chapters_file = None
    want_cards = "--cards" in flags
    want_side = "--side" in flags
    for f in flags:
        if f.startswith("--xfade"):
            xfade = float(f.split("=")[-1]) if "=" in f else 0.45
        if f.startswith("--trans"):
            trans = f.split("=")[-1]
        if f.startswith("--chapters"):
            chapters_file = f.split("=", 1)[-1]
    # 章立ての取得（ファイル優先、無ければ argv の `時刻:見出し`）
    if chapters_file:
        chapters = parse_chapters_file(chapters_file)
    else:
        chapters = [(parse_time(k), h, None, None)
                    for k, h in (s.split(":", 1) for s in args[2:])]
    if not (want_cards or want_side):
        want_cards = True   # 既定は扉絵
    if not chapters:
        sys.exit("章立てが空です")

    W, H = probe_size(video)
    work = video.parent / ".titlecard"
    work.mkdir(exist_ok=True)
    op = [c for c in chapters if c[0] is None]
    secs = sorted([c for c in chapters if c[0] is not None], key=lambda x: x[0])

    # 扉絵を入れない＝サイドラベルのみ（1パス）
    if want_side and not want_cards:
        side_only(video, secs, out, W, H, work)
        print(f"完成: {out}  （サイドテロップのみ {len(secs)}章）", flush=True)
        return

    # 扉絵あり（必要ならサイドラベルも本編区間に重ねる）
    pieces = []
    if op:
        png = work / "card_op.png"
        make_card_png(video, 0.0, op[0][1], W, H, png, op[0][2], op[0][3])
        clip = work / "card_op.mp4"
        card_clip(png, W, H, clip)
        pieces.append(clip)
    prev, cur_label = 0.0, None
    for i, (t, head, img, full) in enumerate(secs):
        seg = work / f"seg{i}.mp4"
        lp = None
        if want_side and cur_label:
            lp = work / f"label{i}.png"
            render_side_label(cur_label, W, H, lp)
        video_segment(video, prev, t, W, H, seg, lp)
        pieces.append(seg)
        png = work / f"card{i}.png"
        make_card_png(video, t, head, W, H, png, img, full)
        clip = work / f"card{i}.mp4"
        card_clip(png, W, H, clip)
        pieces.append(clip)
        cur_label, prev = head, t
    segL = work / "seg_last.mp4"
    lpL = None
    if want_side and cur_label:
        lpL = work / "label_last.png"
        render_side_label(cur_label, W, H, lpL)
    video_segment(video, prev, None, W, H, segL, lpL)
    pieces.append(segL)

    if xfade > 0:
        assemble_xfade(pieces, out, xfade, trans)
    else:
        lst = work / "concat.txt"
        lst.write_text("".join(f"file '{p.resolve()}'\n" for p in pieces), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", str(out)], check=True)
    print(f"完成: {out}  （扉絵{len(secs)+len(op)}枚{' ＋サイドテロップ' if want_side else ''}）",
          flush=True)


if __name__ == "__main__":
    main()
