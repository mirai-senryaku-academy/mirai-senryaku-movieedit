#!/usr/bin/env python3
"""整体プログラム向け 動画自動テロップ付けパイプライン。

工程:
  1. faster-whisper で文字起こし（word単位タイムスタンプ）。任意で無音カット(VAD)。
  2. 用語辞書で誤変換を補正（仙腸関節・筋膜 等）。
  3. BudouX で自然な改行位置に分割し、テロップ(SRT)を生成。
  4. 任意で ffmpeg 焼き込みテロップ動画を出力（After Effects/Premiere 不要）。

依存: faster-whisper, budoux, ffmpeg(brew)。 venv: ./.venv
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import budoux
from faster_whisper import WhisperModel

# 文字起こし1語ぶん。cache読込時もライブ実行時もこの型に統一する。
Word = namedtuple("Word", "start end word")


def transcribe_cached(video: Path, model: str, lang: str, vad: bool, refresh: bool):
    """文字起こしをキャッシュする。同じ動画×モデル×VAD設定なら2回目以降は即返す。

    用語補正と改行は呼び出し側で後段適用するので、辞書を直しても再文字起こしは不要。
    """
    st = video.stat()
    key = hashlib.sha1(
        f"{video.resolve()}|{st.st_size}|{int(st.st_mtime)}|{model}|{lang}|{vad}".encode()
    ).hexdigest()[:16]
    cache_dir = Path(__file__).resolve().parent / ".cache"
    cache_dir.mkdir(exist_ok=True)
    cf = cache_dir / f"{key}.json"

    if cf.exists() and not refresh:
        data = json.loads(cf.read_text(encoding="utf-8"))
        words = [Word(*w) for w in data["words"]]
        print(f"[1/4] 文字起こしキャッシュを使用（{len(words)}語 / {data['duration']:.0f}s）。"
              f"再文字起こしなし。", flush=True)
        return words, data["full_text"], data["duration"]

    print(f"[1/4] 文字起こし開始 model={model} vad={'ON' if vad else 'OFF'} ...", flush=True)
    model_obj = WhisperModel(model, device="cpu", compute_type="int8")
    segments, info = model_obj.transcribe(
        str(video), language=lang, word_timestamps=True, vad_filter=vad)
    words, seg_texts = [], []
    for seg in segments:
        seg_texts.append(seg.text)
        if seg.words:
            words.extend(Word(w.start, w.end, w.word) for w in seg.words)
        else:  # 単語タイムスタンプが無い場合のフォールバック
            words.append(Word(seg.start, seg.end, seg.text))
    full_text = "".join(seg_texts).strip()
    cf.write_text(json.dumps(
        {"words": [list(w) for w in words], "full_text": full_text,
         "duration": info.duration}, ensure_ascii=False), encoding="utf-8")
    print(f"      検出 {len(words)} 語 / 音声長 {info.duration:.0f}s（キャッシュ保存済み）",
          flush=True)
    return words, full_text, info.duration


def download_if_url(arg: str, dest_dir: Path) -> Path:
    """引数がURLなら yt-dlp で落として保存先パスを返す。ローカルパスならそのまま。"""
    if not (arg.startswith("http://") or arg.startswith("https://")):
        return Path(arg).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest_dir / "%(title).80s.%(ext)s")
    print(f"[0/4] URL検出。yt-dlp でダウンロード中 ...", flush=True)
    proc = subprocess.run(
        ["yt-dlp", "--no-playlist", "--no-simulate",
         "--print", "after_move:filepath",
         "-f", "bv*[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4]/b",
         "-o", outtmpl, arg],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"ダウンロード失敗:\n{proc.stderr[-800:]}")
    path = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if not path or not Path(path).exists():
        sys.exit("ダウンロードはできたが保存先パスを特定できなかった。")
    print(f"      保存: {path}", flush=True)
    return Path(path)


def load_term_dict(path: Path):
    """`誤り\t正しい` 形式（または `誤り=正しい`）の補正辞書を読む。"""
    rules = []
    if not path or not path.exists():
        return rules
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            wrong, right = line.split("\t", 1)
        elif "=" in line:
            wrong, right = line.split("=", 1)
        else:
            continue
        rules.append((wrong.strip(), right.strip()))
    return rules


def apply_terms(text: str, rules):
    for wrong, right in rules:
        text = text.replace(wrong, right)
    return text


def fmt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


PARSER = budoux.load_default_japanese_parser()
SENTENCE_END = "。！？!?"


def wrap_lines(text: str, max_chars: int, max_lines: int):
    """BudouX の句境界で max_lines 行に収まるよう改行する（語の途中で切らない）。

    均等割りの予算から始め、句の粒度で溢れたら予算を1字ずつ上げて max_lines に収める。
    """
    import math
    phrases = PARSER.parse(text)

    def pack(budget):
        lines, cur = [], ""
        for ph in phrases:
            if cur and len(cur) + len(ph) > budget:
                lines.append(cur)
                cur = ph
            else:
                cur += ph
        if cur:
            lines.append(cur)
        return lines

    budget = max(1, math.ceil(len(text) / max_lines))
    lines = pack(budget)
    while len(lines) > max_lines and budget < len(text):
        budget += 1
        lines = pack(budget)
    return lines


PUNCT_TAIL = "、。！？!?"


def build_captions(words, max_chars, max_lines, rules):
    """word列を BudouX 句境界＋句読点で自然に区切る。語の途中では切らない。

    - 句点(。！？)で必ず確定。読点(、)はある程度の長さがあれば確定。
    - 1枚が max_chars*max_lines を超えそうなら直前の句境界で確定。
    - 「します。」のような孤立した短いカードは直前へ併合。
    """
    cap_limit = max_chars * max_lines
    soft_min = max(8, max_chars - 4)

    # 生テキストと「文字→時刻」対応表（用語補正で長さが変わる前に作る）
    cmap = [(ch, w.start, w.end) for w in words for ch in w.word]
    full = "".join(c for c, _, _ in cmap)
    if not full:
        return []
    phrases = PARSER.parse(full)

    captions = []
    cur, idx, start_idx = "", 0, 0
    for ph in phrases:
        if cur == "":
            start_idx = idx
        if cur and len(cur) + len(ph) > cap_limit:
            captions.append((cmap[start_idx][1], cmap[idx - 1][2], cur))
            cur, start_idx = "", idx
        cur += ph
        idx += len(ph)
        tail = cur.rstrip()[-1] if cur.rstrip() else ""
        if tail in SENTENCE_END or (tail == "、" and len(cur.strip()) >= soft_min):
            captions.append((cmap[start_idx][1], cmap[idx - 1][2], cur))
            cur = ""
    if cur.strip():
        captions.append((cmap[start_idx][1], cmap[idx - 1][2], cur))

    # 孤立した短いカードを直前に併合（「します。」対策）
    merged = []
    for s, e, t in captions:
        if (merged and len(t.strip().rstrip(PUNCT_TAIL)) <= 4
                and len(merged[-1][2]) + len(t) <= cap_limit + 8):
            ps, _pe, pt = merged[-1]
            merged[-1] = (ps, e, pt + t)
        else:
            merged.append((s, e, t))

    out = []
    for s, e, t in merged:
        t = t.strip().lstrip("、。 ")
        t = apply_terms(t, rules)
        t = t.replace("。", "").rstrip("、")   # テロップは句点なし（末尾の読点も落とす）
        lines = wrap_lines(t, max_chars, max_lines)
        out.append((s, e, "\n".join(lines)))
    return out


CIRCLED = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤",
           6: "⑥", 7: "⑦", 8: "⑧", 9: "⑨"}


def generate_chapters(words, trim_start, path: Path, op_title: str, rules=None):
    """トランスクリプトから章立て候補を自動生成（「N番目」等の合図を拾う）。ハイブリッドの土台。"""
    rules = rules or []
    cmap = [(ch, w.start) for w in words for ch in w.word]
    full = "".join(c for c, _ in cmap)
    z2h = str.maketrans("１２３４５６７８９", "123456789")
    kan = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}
    pat = re.compile(r"([1-9１-９一二三四五六七八九])\s*(?:番目|つ目|番)")
    lines = ["# 時刻   見出し  （[OP]＝冒頭の扉絵。不要なら行を消す。img=画像 / full=完成扉絵 も指定可）",
             f"[OP]    {op_title}"]
    seen = set()
    for m in pat.finditer(full):
        c = m.group(1)
        n = kan[c] if c in kan else int(c.translate(z2h))
        if n in seen:
            continue
        seen.add(n)
        t = max(0.0, cmap[m.start()][1] - trim_start)
        after = full[m.end():m.end() + 14].lstrip("、。 ")
        name = re.split(r"[、。\s]", after)[0][:8] if after else ""
        name = apply_terms(name, rules)   # 用語辞書で章タイトルも補正（警察→軽擦 等）
        lines.append(f"{int(t//60)}:{int(t % 60):02d}    {CIRCLED.get(n, str(n)+'.')} {name}".rstrip())
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return max(0, len(lines) - 2)


def write_srt(captions, path: Path):
    with path.open("w", encoding="utf-8") as f:
        for i, (s, e, text) in enumerate(captions, 1):
            f.write(f"{i}\n{fmt_ts(s)} --> {fmt_ts(e)}\n{text}\n\n")


def burn_in(video: Path, srt: Path, out: Path, font_size: int, font: str):
    """ffmpeg subtitles フィルタでテロップを焼き込む（白文字＋黒縁取り）。

    パスの空白・コロンが filtergraph 構文を壊すので、cwd を srt のフォルダに移して
    basename だけを subtitles= に渡す。force_style 内のカンマは単一引用符で保護する。
    """
    style = (
        f"FontName={font},FontSize={font_size},"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=60"
    )
    vf = f"subtitles={srt.name}:force_style='{style}'"
    cmd = [
        "ffmpeg", "-y", "-i", str(video.resolve()),
        "-vf", vf, "-c:a", "copy", str(out.resolve()),
    ]
    subprocess.run(cmd, check=True, cwd=str(srt.parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="入力動画ファイル")
    ap.add_argument("--model", default="medium", help="whisperモデル (small/medium/large-v3)")
    ap.add_argument("--lang", default="ja")
    ap.add_argument("--vad", action="store_true", help="無音区間カット(Silero VAD)を有効化。既定OFF")
    ap.add_argument("--max-chars", type=int, default=16, help="1行の最大文字数")
    ap.add_argument("--max-lines", type=int, default=2, help="テロップ最大行数")
    ap.add_argument("--terms", default="用語辞書.txt", help="用語補正辞書")
    ap.add_argument("--burn", action="store_true", help="焼き込みテロップ動画も出力")
    ap.add_argument("--font", default="Hiragino Sans", help="焼き込み時フォント名")
    ap.add_argument("--font-size", type=int, default=22)
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--refresh", action="store_true",
                    help="キャッシュを無視して文字起こしし直す")
    ap.add_argument("--style", default="yellow",
                    help="焼き込みスタイル yellow/red/band（キーワード強調色・帯）")
    ap.add_argument("--pop", default="none",
                    help="テロップの動き none(既定)/beats(区切りだけ)/all(毎回)")
    ap.add_argument("--hl", type=int, default=0,
                    help="キーワード強調の最大数/枚（既定0=強調なし）")
    ap.add_argument("--emphasis", default=None,
                    help="指定した語を強調（カンマ区切り）。例: --emphasis 軽擦,血行")
    ap.add_argument("--no-anim", action="store_true",
                    help="（旧）静止オーバーレイ方式で焼く。--pop none と同等")
    ap.add_argument("--no-trim-head", action="store_true",
                    help="冒頭の自動カットを無効化（既定は最初の発話の手前までカット）")
    ap.add_argument("--no-trim-tail", action="store_true",
                    help="末尾の自動カットを無効化")
    ap.add_argument("--head-lead", type=float, default=1.5,
                    help="冒頭カットの余白秒（最初の発話の何秒前まで残すか）")
    ap.add_argument("--tail-pad", type=float, default=1.5,
                    help="末尾カットの余白秒（最後の発話の何秒後まで残すか）")
    ap.add_argument("--titles", action="store_true", help="章の区切りに扉絵を入れる")
    ap.add_argument("--side", action="store_true", help="右上に章名のサイドテロップを常駐")
    ap.add_argument("--bgm", default=None, help="BGMを付ける（bgm/の名前 or ファイルパス）")
    ap.add_argument("--chapters", default=None, help="章立てファイルを明示指定")
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    video = download_if_url(args.video, script_dir / "downloads")
    if not video.exists():
        sys.exit(f"動画が見つからない: {video}")
    outdir = Path(args.outdir) if args.outdir else video.parent
    outdir.mkdir(parents=True, exist_ok=True)
    stem = video.stem

    rules = load_term_dict(Path(args.terms))
    words, full_text, vid_dur = transcribe_cached(
        video, args.model, args.lang, args.vad, args.refresh)

    print("[2/4] 用語補正 + [3/4] BudouX改行 ...", flush=True)
    captions = build_captions(words, args.max_chars, args.max_lines, rules)
    srt_path = outdir / f"{stem}.srt"
    write_srt(captions, srt_path)          # 編集ソフト用＝元動画基準の全尺SRT
    (outdir / f"{stem}.txt").write_text(
        apply_terms(full_text, rules), encoding="utf-8")
    print(f"      SRT: {srt_path}  (テロップ {len(captions)} 枚)", flush=True)

    # 冒頭/末尾の自動カット範囲を発話タイムから決める（焼き込み時のみ適用）
    trim_start, trim_end = 0.0, None
    if words:
        if not args.no_trim_head:
            trim_start = max(0.0, words[0].start - args.head_lead)
        if not args.no_trim_tail:
            trim_end = min(vid_dur, words[-1].end + args.tail_pad)
    burn_srt = srt_path
    if args.burn and (trim_start > 0 or trim_end is not None):
        # 焼き込み用に、カット範囲へ時刻を平行移動＋範囲外を捨てたSRTを作る
        shifted = []
        for s, e, t in captions:
            if trim_end is not None and s >= trim_end:
                continue
            if e <= trim_start:
                continue
            ns, ne = max(0.0, s - trim_start), e - trim_start
            shifted.append((ns, ne, t))
        burn_srt = outdir / f"{stem}.trim.srt"
        write_srt(shifted, burn_srt)
        print(f"      冒頭/末尾カット: {trim_start:.1f}s 〜 "
              f"{'末尾' if trim_end is None else f'{trim_end:.1f}s'}", flush=True)

    if args.burn:
        # このffmpegに字幕焼き込み(libass)が無い場合はクラッシュせず案内する
        filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                                 capture_output=True, text=True).stdout
        out_mp4 = outdir / f"{stem}_telop.mp4"
        if "subtitles" in filters:
            print(f"[4/4] 焼き込み出力(libass) -> {out_mp4} ...", flush=True)
            burn_in(video, srt_path, out_mp4, args.font_size, args.font)
        elif args.no_anim:
            # 静止オーバーレイ（速いがアニメ無し）
            print(f"[4/4] 静止オーバーレイで焼き込み -> {out_mp4} ...", flush=True)
            script = Path(__file__).resolve().parent / "burn_overlay.py"
            r = subprocess.run([sys.executable, str(script), str(video),
                                str(srt_path), str(out_mp4)])
            if r.returncode != 0:
                sys.exit("焼き込み失敗（burn_overlay.py）")
        else:
            # 既定: キーワード強調焼き込み（burn_anim.py）。動きは --pop で制御（既定none）
            # 冒頭/末尾カットは burn_srt(平行移動済み)＋ --start/--end で適用
            print(f"[4/4] 焼き込み(style={args.style}, pop={args.pop}) -> {out_mp4} ...",
                  flush=True)
            script = Path(__file__).resolve().parent / "burn_anim.py"
            cmd = [sys.executable, str(script), str(video),
                   str(burn_srt), str(out_mp4),
                   f"--style={args.style}", f"--pop={args.pop}", f"--hl={args.hl}",
                   f"--start={trim_start}"]
            if args.emphasis:
                cmd.append(f"--kw={args.emphasis}")
            if trim_end is not None:
                cmd.append(f"--end={trim_end}")
            r = subprocess.run(cmd)
            if r.returncode != 0:
                sys.exit("焼き込み失敗（burn_anim.py）")
        print(f"      テロップ完成: {out_mp4}", flush=True)

        final = out_mp4
        # 扉絵 / サイドテロップ（章立てを使う）
        if args.titles or args.side:
            ch_file = Path(args.chapters) if args.chapters else outdir / f"{stem}.章立て.txt"
            if not ch_file.exists():
                n = generate_chapters(words, trim_start, ch_file, stem, rules)
                print(f"      章立てを自動生成: {ch_file}（{n}章。直したい場合は編集して再実行）",
                      flush=True)
            titled = outdir / f"{stem}_titled.mp4"
            tcmd = [sys.executable, str(script_dir / "titlecard.py"),
                    str(final), str(titled), f"--chapters={ch_file}"]
            if args.titles:
                tcmd.append("--cards")
            if args.side:
                tcmd.append("--side")
            print(f"      扉絵/サイドテロップ挿入 -> {titled} ...", flush=True)
            if subprocess.run(tcmd).returncode != 0:
                sys.exit("扉絵/サイドテロップ失敗（titlecard.py）")
            final = titled
        # BGM
        if args.bgm:
            bgmout = outdir / f"{stem}_final.mp4"
            print(f"      BGM付与({args.bgm}) -> {bgmout} ...", flush=True)
            if subprocess.run([sys.executable, str(script_dir / "add_bgm.py"),
                               str(final), args.bgm, str(bgmout)]).returncode != 0:
                sys.exit("BGM付与失敗（add_bgm.py）")
            final = bgmout
        print(f"      ★完成: {final}", flush=True)
    else:
        print("[4/4] 焼き込みはスキップ（--burn で有効化）。SRTを編集ソフトに読み込んで使う。", flush=True)


if __name__ == "__main__":
    main()
