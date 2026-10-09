#!/bin/zsh
# MediaMTX + backend + AI service (+ Cloudflare tunnel)-ийг macOS Terminal-д ажиллуулна (давхар товшоход болно). Зогсоох: ⌃C
# Шинэ Mac дээр анх удаа асаахад шаардлагатай програмуудыг автоматаар суулгана.
cd "$(dirname "$0")" || exit 1
if [[ ! -x bin/mediamtx || ! -x bin/cloudflared || ! -x backend/.venv/bin/python || ! -x ai-service/.venv/bin/python ]]; then
  ./scripts/setup-mac.sh || exit 1
fi
python3 worker.py
