#!/usr/bin/env bash
# Download the two public sample pages into pages/ (not kept in git).
#   ./fetch.sh   (needs curl and pdftoppm from poppler)
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p pages
UA="gramps-web-toolkit-ocr-bench/0.1 (+https://github.com/GeiserX/gramps-web-toolkit)"

# Gaceta de Madrid, 1 January 1885, page 3 (BOE-A-1885-10). Reuse allowed by the BOE with attribution.
if [ ! -s pages/gaceta-1885-01-01-p3.png ]; then
  curl -fsSL -A "$UA" -o pages/gaceta.pdf https://www.boe.es/datos/pdfs/BOE//1885/001/A00003-00003.pdf
  pdftoppm -r 300 -png -singlefile pages/gaceta.pdf pages/gaceta-1885-01-01-p3
  rm pages/gaceta.pdf
fi

# Baptism of Bartolomé Esteban Murillo, Seville 1618 (photo by Anual, CC BY-SA 4.0, Wikimedia Commons).
if [ ! -s pages/murillo-1618-bautismo.jpg ]; then
  curl -fsSL -A "$UA" -o pages/murillo-1618-bautismo.jpg \
    https://upload.wikimedia.org/wikipedia/commons/5/5d/Libro_bautismo_murillo_2018001.jpg
  echo "54282410307b4f738737d9b71ff6b7af7d336753  pages/murillo-1618-bautismo.jpg" | shasum -c -
fi
ls -l pages
