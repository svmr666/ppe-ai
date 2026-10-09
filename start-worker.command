#!/bin/zsh
# MediaMTX + backend + AI service-ийг macOS Terminal-д ажиллуулна (давхар товшоход болно). Зогсоох: ⌃C
cd "$(dirname "$0")" && python3 worker.py
