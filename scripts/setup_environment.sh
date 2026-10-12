#!/usr/bin/env bash
set -euo pipefail
cd /workspace/golden-city-visualization
command -v blender
command -v chromium
command -v ffmpeg
command -v pdfinfo
command -v pdftoppm
python -m venv .venv
.venv/bin/python -m pip install --no-cache-dir -r requirements-dev.txt
npm ci --cache /tmp/golden-npm-cache --ignore-scripts --no-audit --no-fund
npm run vendor
.venv/bin/python -m pip check
.venv/bin/python scripts/test_report.py
