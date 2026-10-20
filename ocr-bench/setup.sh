#!/usr/bin/env bash
# Build one virtualenv per engine and fetch the models the bench uses.
# Run it on the box that does the OCR work, never on a laptop that is already busy.
#
#   BENCH_HOME=/Volumes/Data/scratch/ocr-bench ./setup.sh [engine ...]
#
# Engines: tesseract kraken trocr paddle doctr surya. No argument = all of them.
# Everything (venvs, model caches, tessdata) lands under $BENCH_HOME, so deleting
# that one folder removes every byte this script downloaded.
set -euo pipefail

BENCH_HOME=${BENCH_HOME:?set BENCH_HOME to a scratch folder with ~15 GB free}
PY=${BENCH_PYTHON:-3.12}
mkdir -p "$BENCH_HOME"/{venv,cache,models,tessdata}
export HF_HOME=$BENCH_HOME/cache/hf
export TORCH_HOME=$BENCH_HOME/cache/torch
export PADDLE_PDX_CACHE_HOME=$BENCH_HOME/cache/paddlex
export XDG_CACHE_HOME=$BENCH_HOME/cache/xdg
export UV_CACHE_DIR=$BENCH_HOME/cache/uv

venv() { # venv <name> <pip packages...>
  local name=$1; shift
  [ -x "$BENCH_HOME/venv/$name/bin/python" ] || uv venv -q -p "$PY" "$BENCH_HOME/venv/$name"
  uv pip install -q -p "$BENCH_HOME/venv/$name/bin/python" "$@"
}

fetch() { # fetch <url> <dest>
  [ -s "$2" ] || curl -fsSL --retry 3 -o "$2" "$1"
}

setup_tesseract() {
  command -v tesseract >/dev/null || { echo "install tesseract 5 first (brew install tesseract / apt install tesseract-ocr)"; exit 1; }
  # tessdata_best: the slower, more accurate LSTM models. tessdata_fast is what most packages ship.
  for l in spa lat eng osd; do
    fetch "https://github.com/tesseract-ocr/tessdata_best/raw/main/$l.traineddata" "$BENCH_HOME/tessdata/$l.traineddata"
  done
  venv tesseract pillow
}

setup_kraken() {
  venv kraken "kraken>=5.3"
  # McCATMuS: one HTR model trained on 16th-21st century manuscripts and prints in
  # Spanish, French, Latin, Italian and more (HTR-United / Zenodo 13788177).
  local d=$BENCH_HOME/models/kraken
  mkdir -p "$d"
  fetch "https://zenodo.org/records/13788177/files/McCATMuS_nfd_nofix_V1.mlmodel?download=1" "$d/mccatmus.mlmodel"
}

setup_trocr() {
  venv trocr "torch" "transformers>=4.44,<5" pillow sentencepiece "kraken>=5.3"
}

setup_paddle() {
  venv paddle "paddlepaddle>=3.0" "paddleocr>=3.0"
}

setup_doctr() {
  venv doctr "python-doctr[torch]>=0.10"
}

setup_surya() {
  venv surya "surya-ocr>=0.14"
}

engines=${*:-tesseract kraken trocr paddle doctr surya}
for e in $engines; do
  echo "== $e"
  "setup_$e"
done
echo "done: $(du -sh "$BENCH_HOME" | cut -f1) in $BENCH_HOME"
