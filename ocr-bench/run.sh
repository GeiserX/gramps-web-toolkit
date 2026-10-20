#!/usr/bin/env bash
# Run every engine over a folder of staged page images (see stage.py).
#
#   BENCH_HOME=/Volumes/Data/scratch/ocr-bench ./run.sh PAGES OUT [engine ...]
#
# Engines: tesseract kraken trocr-hist trocr-es paddle doctr surya (default: all, in that
# order; trocr-* reuse the lines kraken found, so kraken runs first). Pages that already
# have output are skipped, so a run can be resumed. Each engine writes OUT/<engine>/.
set -euo pipefail
trap 'kill 0 2>/dev/null' EXIT   # nothing an engine started outlives the run

BENCH_HOME=${BENCH_HOME:?set BENCH_HOME (the folder setup.sh filled)}
PAGES=$(cd "$1" && pwd); OUT=$(mkdir -p "$2" && cd "$2" && pwd); shift 2
HERE=$(cd "$(dirname "$0")" && pwd)
V=$BENCH_HOME/venv
export HF_HOME=$BENCH_HOME/cache/hf TORCH_HOME=$BENCH_HOME/cache/torch \
  PADDLE_PDX_CACHE_HOME=$BENCH_HOME/cache/paddlex XDG_CACHE_HOME=$BENCH_HOME/cache/xdg \
  PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True TESSDATA_DIR=$BENCH_HOME/tessdata \
  TOKENIZERS_PARALLELISM=false PYTORCH_ENABLE_MPS_FALLBACK=1
DEV=cpu
[ "$(uname -s)" = Darwin ] && DEV=mps
command -v nvidia-smi >/dev/null 2>&1 && DEV=cuda

shopt -s nullglob
imgs=("$PAGES"/*.jpg "$PAGES"/*.jpeg "$PAGES"/*.png "$PAGES"/*.tif)
E=$HERE/engines
for engine in ${*:-tesseract kraken trocr-hist trocr-es paddle doctr surya}; do
  echo "== $engine $(date +%T)"
  case $engine in
    tesseract) "$V/tesseract/bin/python" "$E/tesseract.py" --lang spa --out "$OUT/tesseract" "${imgs[@]}" ;;
    kraken) "$V/kraken/bin/python" "$E/kraken_htr.py" --model "$BENCH_HOME/models/kraken/mccatmus.mlmodel" \
              --device "$DEV" --out "$OUT/kraken" "${imgs[@]}" ;;
    trocr-hist) "$V/trocr/bin/python" "$E/trocr.py" --model nikk6174/historical-spanish-trocr \
              --seg "$OUT/kraken/seg" --device "$DEV" --out "$OUT/trocr-hist" "${imgs[@]}" ;;
    trocr-es) "$V/trocr/bin/python" "$E/trocr.py" --model ifesther/trocr-spanish-handwritten \
              --seg "$OUT/kraken/seg" --device "$DEV" --out "$OUT/trocr-es" "${imgs[@]}" ;;
    paddle) "$V/paddle/bin/python" "$E/paddle_ocr.py" --lang es --out "$OUT/paddle" "${imgs[@]}" ;;
    doctr) "$V/doctr/bin/python" "$E/doctr_ocr.py" --device "$DEV" --out "$OUT/doctr" "${imgs[@]}" ;;
    surya) "$V/surya/bin/python" "$E/surya_ocr.py" --out "$OUT/surya" "${imgs[@]}" ;;
    *) echo "unknown engine $engine" >&2; exit 2 ;;
  esac
done
