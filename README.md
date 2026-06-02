# 整体プログラム 動画編集スキル

Claude Code を入れた人が、**スマホで撮った動画を渡して日本語で頼むだけ**で
テロップ・扉絵・サイドテロップ・BGM入りの動画を仕上げるための一式。
After Effects / Premiere は不要。プログラミングの知識も不要。

→ 使う人向けの手順は **[動画編集スキルガイド.md](動画編集スキルガイド.md)**（基礎/中級/上級）。
→ 撮影の決まりは **[撮影ルール.md](撮影ルール.md)**。

## できること

- **テロップ**：冒頭/末尾の自動カット → 文字起こし → 用語補正 → 自然な改行 → 焼き込み（1080p高画質・Noto Sans JP Black）
- **扉絵**：章の区切りにタイトル画面（章は自動検出、`章立て.txt`で編集可）
- **サイドテロップ**：右上に章名を常駐
- **BGM**：同梱のCC0音源 or 持ち込み（ナレーション中は自動で音量を下げる）
- **カット編集**：「1:20〜1:35を切って」で任意区間を削除
- **強調**：「"軽擦"を強調して」で指定語を色付け

すべて日本語プロンプトで操作（対応は `SKILL.md` がClaudeに教える）。

## 中身

| ファイル/フォルダ | 役割 |
|---|---|
| `SKILL.md` | プロンプト→処理の対応表（Claude Codeが読む） |
| `edit_video.py` | 本体（カット→文字起こし→補正→改行→焼き込み→扉絵/サイド/BGMの統合） |
| `titlecard.py` | 扉絵・サイドテロップ生成 |
| `add_bgm.py` | BGMミックス（ダッキング） |
| `cut_ranges.py` | 任意区間カット |
| `用語辞書.txt` | 専門用語の誤変換補正（育てると精度UP） |
| `fonts/` | Noto Sans JP（OFLライセンス同梱） |
| `bgm/` | CC0音源＋`CREDITS.md` |

## 初回セットアップ（1回だけ）

Claude Code に「このフォルダで Python3.12 の venv を作って faster-whisper と budoux と Pillow を入れて」と頼むか、手動で:
```bash
brew install ffmpeg yt-dlp
uv venv --python 3.12 .venv
.venv/bin/python -m pip install faster-whisper budoux Pillow   # or: uv pip install ...
```

## 使い方（最短）

```bash
.venv/bin/python edit_video.py "撮った動画.mp4" --burn
```
全部入り:
```bash
.venv/bin/python edit_video.py "撮った動画.mp4" --burn --titles --side --bgm forest
```

## ライセンス

- 同梱フォント Noto Sans JP … SIL OFL 1.1（`fonts/OFL.txt`）
- 同梱BGM … CC0（`bgm/CREDITS.md`）
- コード … （リポジトリのLICENSEに従う）
