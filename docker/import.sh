#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sha256sum --check SHA256SUMS
# tar checksum establishes transfer consistency, not publisher authenticity.
test ! -e .runtime/models || { echo 'Model directory exists; import into a new directory.' >&2; exit 1; }
docker image load --input images.tar.gz
bash docker/run.sh setup
tar -xzf models.tar.gz -C .runtime/models --no-same-owner
echo 'Imported images and OCR cache. Next: bash docker/run.sh login'
