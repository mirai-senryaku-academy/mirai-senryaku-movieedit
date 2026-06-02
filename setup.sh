#!/usr/bin/env bash
# 初回セットアップ（1回だけ）。このスキルのフォルダで実行する。
# 使い方:  bash setup.sh
set -e
cd "$(dirname "$0")"

echo "== ffmpeg / yt-dlp =="
if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then brew install ffmpeg; else echo "ffmpegを入れてください（apt install ffmpeg 等）"; fi
fi
if ! command -v yt-dlp >/dev/null; then
  if command -v brew >/dev/null; then brew install yt-dlp; else echo "yt-dlpを入れてください"; fi
fi

echo "== Python venv（faster-whisper / budoux / Pillow）=="
if command -v uv >/dev/null; then
  uv venv --python 3.12 .venv
  . .venv/bin/activate
  uv pip install faster-whisper budoux Pillow
else
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install faster-whisper budoux Pillow
fi

echo "== 完了 =="
echo "使い方:  .venv/bin/python edit_video.py \"動画.mp4\" --burn"
echo "（文字起こしモデルは初回実行時に自動DLされます）"
