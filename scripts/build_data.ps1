# Rebuild the comparison dataset (uses cached pages; add -Refresh to re-download).
# Run from the repo root:  .\scripts\build_data.ps1
param([switch]$Refresh)

if ($Refresh) { python -m src.pipeline.build_dataset --refresh }
else { python -m src.pipeline.build_dataset }
python -m src.pipeline.build_stores
