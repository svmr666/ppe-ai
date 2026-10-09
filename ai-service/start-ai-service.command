#!/bin/zsh
# macOS Terminal-д нээгдэж AI service-ийг ажиллуулна (камерын зөвшөөрөл Terminal-аас асуугдана)
cd "$(dirname "$0")" && source .venv/bin/activate && python main.py serve
