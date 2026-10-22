#!/bin/bash
# Prove automatiche con servizi finti (nessuna chiave, nessun costo). Da lanciare dalla cartella principale.
set -e
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
for t in tests/prova_*.py; do
  echo "=== $t"
  mkdir -p "$TMP/$(basename $t .py)"
  python3 "$t" "$TMP/$(basename $t .py)" 2>&1 | grep -v -iE "warn|INFO apscheduler" | tail -15
done
echo "=== tutte le prove eseguite"
