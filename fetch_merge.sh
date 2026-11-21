#!/usr/bin/env bash
# Download Alpoge's Lean proof (lean.tar.gz.part01..14), merge, extract.
set -euo pipefail
mkdir -p download/parts && cd download
for i in $(seq -w 1 14); do
  p=parts/lean.tar.gz.part$i
  if [ -s "$p" ]; then echo "part$i exists, skipping"; continue; fi
  echo "Downloading part$i"
  curl -fL --progress-bar -o "$p.tmp" "https://alpo.ge/lean.tar.gz.part$i" && mv "$p.tmp" "$p"
done
if gzip -t lean.tar.gz 2>/dev/null; then echo "lean.tar.gz is valid, skipping merge"
else echo "Merging parts into lean.tar.gz"; cat parts/lean.tar.gz.part?? > lean.tar.gz; gzip -t lean.tar.gz; fi
echo "Extracting lean.tar.gz"
tar -xzf lean.tar.gz -C ..
