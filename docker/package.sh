#!/usr/bin/env bash
# Packager needs Python 3, Docker Compose, sha256sum and tar. Recipient needs Docker.
set -euo pipefail
cd "$(dirname "$0")/.."
destination="${1:-dist/valvue3-docker}"
test ! -e "$destination" || { echo 'Destination exists; choose a new one.' >&2; exit 1; }
mkdir -p "$destination/docker" "$destination/docs"
destination="$(cd "$destination" && pwd)"
# Resolve image names only. Never copy .env, auth, logs, installers or projects.
docker compose config --format json | python3 -c '
import json,sys,pathlib
config=json.load(sys.stdin); out=pathlib.Path(sys.argv[1]); names=[]
with (out/".env.example").open("w") as f:
 for service,key in (("diagnostic","DIAGNOSTIC_IMAGE"),("ocr","OCR_IMAGE")):
  image=config["services"][service]["image"]
  if "\n" in image: raise ValueError("invalid image name")
  f.write(f"{key}={image}\n"); names.append(image)
 f.write("LOCAL_UID=1000\nLOCAL_GID=1000\nPROJECTS_DIR=./离线诊断/projects\nSTATE_DIR=./.runtime\nOCR_THREADS=4\n")
(out/"image-names.txt").write_text("\n".join(names)+"\n")
' "$destination"
mapfile -t images < "$destination/image-names.txt"
docker image save "${images[@]}" | gzip > "$destination/images.tar.gz"
# Requires a warmed, healthy service. Export cache only, never the Codex volume.
docker compose up -d --wait --wait-timeout 1800 ocr
docker compose run --rm --no-deps --entrypoint tar ocr -C /opt/models -czf - . > "$destination/models.tar.gz"
cp compose.yaml compose.isolation.yaml "$destination/"
cp docker/run.sh docker/import.sh docker/valvue3-diagnostic.apparmor "$destination/docker/"
cp docs/DOCKER.md docs/KNOWN_ISSUES.md docs/ACCEPTANCE.md "$destination/docs/"
(
  cd "$destination"
  sha256sum images.tar.gz models.tar.gz compose.yaml compose.isolation.yaml \
    .env.example docker/run.sh docker/import.sh docker/valvue3-diagnostic.apparmor docs/DOCKER.md docs/KNOWN_ISSUES.md \
    docs/ACCEPTANCE.md image-names.txt > SHA256SUMS
)
echo "Bundle ready: $destination"
echo 'Distribute only after verifying rights for the code, WPS, dependencies and models.'
