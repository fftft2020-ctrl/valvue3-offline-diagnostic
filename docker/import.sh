#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sha256sum --check SHA256SUMS
# tar checksum establishes transfer consistency, not publisher authenticity.
if [[ -f models.tar.gz ]]; then
  test ! -e .runtime/models || { echo 'Model directory exists; import into a new directory.' >&2; exit 1; }
fi
docker image load --input images.tar.gz
bash docker/run.sh setup
if [[ -f models.tar.gz ]]; then
  tar -xzf models.tar.gz -C .runtime/models --no-same-owner
fi
echo 'Imported images and optional OCR cache. Next: bash docker/run.sh login'
