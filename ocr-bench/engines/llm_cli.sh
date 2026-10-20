#!/usr/bin/env bash
# «LLM reading» as one more engine: a CLI agent reads each page image cold, with no other
# context, and its transcription is scored like any OCR output.
#
#   ./llm_cli.sh OUT PAGE.jpg [PAGE.jpg ...]
#
# Default agent: Claude Code headless (`claude -p`), model from LLM_MODEL (default opus).
# Set LLM_CMD to use another CLI; it gets the prompt as its last argument and runs in the
# page's folder. Writes OUT/<page id>.txt and OUT/timing.tsv; existing pages are skipped.
set -euo pipefail
OUT=$(mkdir -p "$1" && cd "$1" && pwd); shift
PROMPT='Read the image file ./%s. It is a scanned page of a historical Spanish document (parish book, civil register, notarial deed or newspaper). Transcribe every line of text on the page, in reading order, exactly as written: keep the original spelling and abbreviations, do not expand or modernise, one output line per written line, margin notes where they appear. Output only the transcription, no commentary.'
for img in "$@"; do
  id=$(basename "${img%.*}")
  [ -s "$OUT/$id.txt" ] && continue
  t0=$(date +%s)
  # shellcheck disable=SC2059
  p=$(printf "$PROMPT" "$(basename "$img")")
  if (cd "$(dirname "$img")" && if [ -n "${LLM_CMD:-}" ]; then $LLM_CMD "$p"; else
        claude -p --model "${LLM_MODEL:-opus}" --allowedTools Read --output-format text "$p"; fi) \
      > "$OUT/$id.tmp" 2>/dev/null && [ -s "$OUT/$id.tmp" ]; then
    mv "$OUT/$id.tmp" "$OUT/$id.txt"
    printf '%s\t%s\n' "$id" "$(( $(date +%s) - t0 ))" >> "$OUT/timing.tsv"
    echo "$id $(( $(date +%s) - t0 ))s" >&2
  else
    rm -f "$OUT/$id.tmp"; printf '%s\tERROR\n' "$id" >> "$OUT/timing.tsv"; echo "$id failed" >&2
  fi
done
