#!/usr/bin/env python3
"""テロップをポップ表示アニメ付きで焼き込む（libass不要）。

テロップ層を1フレームずつ Pillow で描き、rawvideo として ffmpeg に流し込んで
元動画に overlay 合成する。出る瞬間に拡大＋軽いオーバーシュートで"ポップ"する。
キーワード（数字・整体用語）は強調色。スタイルは STYLES で切替。

使い方:
  burn_anim.py <動画> <SRT> [出力mp4] [--style yellow|red|band] [--sample]
"""
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 既定フォント = 同梱の Noto Sans JP（可変）。スキルごと持ち運べる。
FONT_PATH = str(Path(__file__).resolve().parent / "fonts" / "NotoSansJP-VF.ttf")
FONT_WEIGHT = "Black"   # Thin/Light/Regular/Medium/SemiBold/Bold/ExtraBold/Black
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


def load_font(size: int):
    f = ImageFont.truetype(FONT_PATH, size)
    try:
        f.set_variation_by_name(FONT_WEIGHT)
    except Exception:
        pass
    return f

STYLES = {
    "yellow": {"accent": (255, 222, 0, 255), "band": False},
    "red":    {"accent": (255, 60, 40, 255), "band": False},
    "band":   {"accent": (255, 222, 0, 255), "band": True},
}
POP_DUR = 0.14          # ポップにかける秒数
POP_FROM = 0.55         # 開始スケール
POP_OVERSHOOT = 1.06    # 行き過ぎの最大スケール


def ease_pop(p: float) -> float:
    """0→1 の進行 p をスケール倍率へ。前半で拡大→少し行き過ぎ→1.0へ。"""
    if p >= 1:
        return 1.0
    # オーバーシュート付き ease-out back
    import math
    s = POP_FROM + (POP_OVERSHOOT - POP_FROM) * (1 - (1 - p) ** 2)
    if p > 0.6:  # 後半で 1.0 に収束
        q = (p - 0.6) / 0.4
        s = POP_OVERSHOOT + (1.0 - POP_OVERSHOOT) * q
    return s


# 強調する核キーワードだけに絞る（汎用語は入れない＝強調が多すぎないように）。
# 案件ごとにここを編集して増減する。数字は別途自動で拾う。
CORE_KEYWORDS = ["軽擦", "揉み方", "手技", "施術", "血行", "圧迫", "叩打", "振戦"]
MAX_HL = 0  # 既定は強調なし。--hl N で1枚あたり最大N個まで強調


def make_kre(keywords=None, numbers=True):
    kws = keywords if keywords else CORE_KEYWORDS
    parts = [re.escape(k) for k in sorted(kws, key=len, reverse=True) if k]
    if numbers:
        parts.append(r"\d+(?:番目|つ|本|回|秒|分|％|%)?")
        parts.append(r"[一二三四五六七八九十百]+(?:番目|つ|回|本)")
    return re.compile("|".join(parts)) if parts else re.compile(r"(?!x)x")


def segment_line(line, kre, budget):
    """budget=[残り強調数]。残りがある間だけ強調スパンにする（くどさ抑制）。"""
    spans, pos = [], 0
    for m in kre.finditer(line):
        if budget[0] <= 0:
            break
        if m.start() > pos:
            spans.append((line[pos:m.start()], False))
        spans.append((m.group(), True))
        budget[0] -= 1
        pos = m.end()
    if pos < len(line):
        spans.append((line[pos:], False))
    return spans or [(line, False)]


def probe(video):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,nb_frames,duration",
         "-of", "json", str(video)], capture_output=True, text=True)
    import json
    s = json.loads(out.stdout)["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    dur = float(s.get("duration", 0)) or 0
    return int(s["width"]), int(s["height"]), fps, dur


def parse_srt(path):
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
        caps.append((start, end, "\n".join(lines[2:])))
    return caps


def render_caption(text, font, kre, style):
    """1枚のテロップを「ちょうど収まる透過画像」に描いて返す（settled状態）。"""
    lines = text.split("\n")
    asc, desc = font.getmetrics()
    lh = asc + desc + int(font.size * 0.30)
    stroke = max(3, int(font.size * 0.14))
    pad = stroke + int(font.size * 0.25)
    probe_img = Image.new("RGBA", (8, 8))
    pd = ImageDraw.Draw(probe_img)
    line_w = [pd.textlength(ln, font=font) for ln in lines]
    W = int(max(line_w) + pad * 2)
    Hh = int(lh * len(lines) + pad * 2)
    img = Image.new("RGBA", (W, Hh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if style["band"]:
        d.rounded_rectangle([0, 0, W, Hh], radius=int(font.size*0.2), fill=(0, 0, 0, 120))
    budget = [MAX_HL]   # 1枚あたりの強調数をここで制限（行をまたいで共有）
    for i, ln in enumerate(lines):
        y = pad + i * lh
        x = pad + (max(line_w) - line_w[i]) / 2
        for span, is_key in segment_line(ln, kre, budget):
            color = style["accent"] if is_key else WHITE
            d.text((x, y), span, font=font, fill=color,
                   stroke_width=stroke, stroke_fill=BLACK)
            x += d.textlength(span, font=font)
    return img


def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    flags = [x for x in sys.argv[1:] if x.startswith("--")]
    if len(a) < 2:
        sys.exit("使い方: burn_anim.py <動画> <SRT> [出力mp4] [--style yellow|red|band] [--sample]")
    video, srt = Path(a[0]), Path(a[1])
    out = Path(a[2]) if len(a) > 2 else video.with_name(video.stem + "_telop.mp4")
    global MAX_HL
    style_name = "yellow"
    pop_mode = "none"   # 既定アニメ無し。all=毎回 / beats=区切りの頭だけ / none=静止
    start = 0.0         # 冒頭カット開始位置（ソースのこの時刻から読む）
    end = None          # 末尾カット位置
    for f in flags:
        if f.startswith("--style"):
            style_name = f.split("=")[-1] if "=" in f else "yellow"
        if f.startswith("--pop"):
            pop_mode = f.split("=")[-1] if "=" in f else "beats"
        if f.startswith("--hl"):
            MAX_HL = int(f.split("=")[-1]) if "=" in f else 2
        if f.startswith("--start"):
            start = float(f.split("=")[-1])
        if f.startswith("--end"):
            end = float(f.split("=")[-1])
    # 指定語強調: --kw=軽擦,血行 が来たらその語だけを強調（数字の自動強調は止める）
    kw_list = None
    for f in flags:
        if f.startswith("--kw") and "=" in f:
            kw_list = [w for w in f.split("=", 1)[1].split(",") if w.strip()]
    if kw_list:
        MAX_HL = 99
    sample = "--sample" in flags
    style = STYLES.get(style_name, STYLES["yellow"])

    sw, sh, fps, dur = probe(video)
    seg_dur = (end - start) if end is not None else (dur - start)   # 出力する尺
    # テロップは別レイヤーなので出力を720p以上に上げて文字をくっきり描く
    # （元映像は引き伸ばしでボケるが、文字はシャープになる）
    target_h = 0
    crf = 18
    preset = "veryfast"
    for f in flags:
        if f.startswith("--height"):
            target_h = int(f.split("=")[-1])
        if f.startswith("--crf"):
            crf = int(f.split("=")[-1])
        if f.startswith("--preset"):
            preset = f.split("=")[-1]
    if target_h == 0:
        target_h = max(sh, 1080)
    H = target_h
    W = (sw * H // sh) // 2 * 2     # アスペクト比維持・偶数
    font_size = max(24, int(H * 0.072))
    font = load_font(font_size)
    kre = make_kre(kw_list, numbers=False) if kw_list else make_kre()
    caps = parse_srt(srt)

    # --sample: 各スタイルで代表テロップの静止画だけ出して終わり
    if sample:
        demo = caps[0][2] if caps else "1番目、軽擦で血行を良くする"
        bg = Image.new("RGBA", (W, H), (40, 40, 48, 255))
        for name, st in STYLES.items():
            im = render_caption(demo, font, kre, st)
            canvas = bg.copy()
            canvas.alpha_composite(im, ((W - im.width)//2, int(H*0.62)))
            p = video.parent / f"_style_{name}.png"
            canvas.convert("RGB").save(p)
            print("sample:", p, flush=True)
        return

    nframes = int(round(seg_dur * fps)) if seg_dur else 0
    bottom_cy = int(H * 0.86)   # テロップ中心の縦位置
    blank = Image.new("RGBA", (W, H), (0, 0, 0, 0)).tobytes()

    # ポップさせるキャプションを決める（くどさ対策）
    SENT_END = "。！？!?"
    pop_flag = []
    for i, (s, e, t) in enumerate(caps):
        if pop_mode == "none":
            pop_flag.append(False)
        elif pop_mode == "all":
            pop_flag.append(True)
        else:  # beats: 文頭 or 前から0.4秒以上空いた頭だけポップ
            if i == 0:
                pop_flag.append(True)
            else:
                gap = s - caps[i-1][1]
                prev_end = caps[i-1][2].rstrip()[-1:] in SENT_END
                pop_flag.append(gap >= 0.4 or prev_end)

    # 各キャプションの settled 画像と合成済みフルフレームをキャッシュ
    settled = [render_caption(t, font, kre, style) for (_s, _e, t) in caps]
    full_cache = {}

    def full_for(i, scale):
        im = settled[i]
        if scale != 1.0:
            w, h = max(1, int(im.width*scale)), max(1, int(im.height*scale))
            im = im.resize((w, h), Image.LANCZOS)
        frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        x = (W - im.width)//2
        y = bottom_cy - im.height//2
        frame.alpha_composite(im, (x, y))
        return frame.tobytes()

    # 元映像を出力解像度へ拡大(lanczos)→その上にくっきりテロップを重ねる。
    # 文字エッジが綺麗に残るよう libx264 高画質(crf18)でエンコード。
    # 冒頭/末尾カット: ソース入力を -ss start -t seg_dur で切って読む
    src_in = ["-i", str(video.resolve())]
    if start > 0 or end is not None:
        src_in = ["-ss", f"{start}", "-t", f"{seg_dur}", "-i", str(video.resolve())]
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           *src_in,
           "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", f"{fps}",
           "-i", "pipe:0",
           "-filter_complex",
           f"[0:v]scale={W}:{H}:flags=lanczos[bg];[bg][1:v]overlay=0:0:format=auto[v]",
           "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", str(out.resolve())]
    npop = sum(pop_flag)
    trim_note = f" / カット {start:.1f}s〜{'末尾' if end is None else f'{end:.1f}s'}" if (start > 0 or end is not None) else ""
    print(f"出力 {W}x{H}(元{sw}x{sh}) {fps:.2f}fps {nframes}フレーム / テロップ{len(caps)}枚 / "
          f"スタイル={style_name} / ポップ={pop_mode}({npop}枚) / libx264 crf{crf} {preset}{trim_note}", flush=True)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    ci = 0
    for f in range(nframes):
        t = f / fps
        while ci < len(caps) and t >= caps[ci][1]:
            ci += 1
        if ci < len(caps) and caps[ci][0] <= t < caps[ci][1]:
            age = t - caps[ci][0]
            if pop_flag[ci] and age < POP_DUR:
                buf = full_for(ci, ease_pop(age / POP_DUR))   # ポップ中（毎フレーム計算）
            else:
                if ci not in full_cache:
                    full_cache[ci] = full_for(ci, 1.0)         # settled をキャッシュ
                buf = full_cache[ci]
        else:
            buf = blank
        proc.stdin.write(buf)
    proc.stdin.close()
    rc = proc.wait()
    if rc != 0:
        sys.exit("ffmpeg失敗 rc=%d" % rc)
    print(f"完成: {out}", flush=True)


if __name__ == "__main__":
    main()
