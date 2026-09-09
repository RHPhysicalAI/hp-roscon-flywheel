#!/bin/bash
# This project was developed with assistance from AI tools.
# Fails if any tracked Python, shell, or Dockerfile source lacks the Red Hat AI-assistance marker.
# Run from the repository root: tools/ci/check-ai-marker.sh
set -uo pipefail
MARKER="developed with assistance from AI tools"
missing=0; total=0
for f in $(git ls-files | grep -E '\.(py|sh)$|(^|/)Dockerfile[^/]*$'); do
  total=$((total+1))
  grep -q "$MARKER" "$f" || { echo "missing marker: $f"; missing=$((missing+1)); }
done
if [ "$missing" -eq 0 ]; then echo "ai-marker: all $total files marked"; else exit 1; fi
