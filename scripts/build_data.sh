#!/usr/bin/env bash
# Rebuild the comparison dataset (uses cached pages; pass --refresh to re-download).
# Run from the repo root:  ./scripts/build_data.sh
set -euo pipefail
python -m src.pipeline.build_dataset "$@"
python -m src.pipeline.build_stores
