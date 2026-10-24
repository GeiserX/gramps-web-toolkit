# ocr-bench

Genealogy research produces piles of page images: parish books, civil registers, notarial deeds, old newspapers. Before someone reads a page closely, it helps to know what is on it. `ocr-bench` runs several free OCR and handwriting (HTR) engines over the same pages and scores each one against a reference transcription, so you can see which engine, if any, is good enough to tell you which pages deserve a close read.

The score that matters most for genealogy is **names found**: of the person and place names in the reference, how many come out of the engine recognisably. CER and WER are reported too.

## What is in the folder

| File | What it does |
|---|---|
| [`setup.sh`](https://github.com/GeiserX/gramps-web-toolkit/blob/main/ocr-bench/setup.sh) | Builds one virtualenv per engine and downloads the models, all under `$BENCH_HOME`. |
| [`stage.py`](https://github.com/GeiserX/gramps-web-toolkit/blob/main/ocr-bench/stage.py) | Copies each reference's page image to `PAGES/<page id>.<ext>`. |
| [`run.sh`](https://github.com/GeiserX/gramps-web-toolkit/blob/main/ocr-bench/run.sh) | Runs the engines over `PAGES`, writing `OUT/<engine>/<page id>.txt` and `timing.tsv`. |
| `engines/` | One small adapter per engine, plus `llm_cli.sh` for an LLM reading. |
| [`score.py`](https://github.com/GeiserX/gramps-web-toolkit/blob/main/ocr-bench/score.py) | Scores every folder in `OUT` against the references and prints the tables. |
| `samples/` | Two public pages with references, to try the whole loop. |

## Engines

| Engine | Folder | What it is | Runs on |
|---|---|---|---|
| Tesseract 5 | `tesseract` | `spa` model from tessdata_best (LSTM). Built for print. | CPU |
| Kraken 7 + McCATMuS | `kraken` | Baseline segmentation (blla) plus the McCATMuS HTR model (Zenodo 13788177), trained on 16th-21st century manuscripts and prints in Spanish, French, Latin and more. | CPU, Apple GPU |
| TrOCR, historical Spanish | `trocr-hist` | `nikk6174/historical-spanish-trocr` on the lines Kraken found. | CPU, Apple GPU, CUDA |
| TrOCR, Spanish handwriting | `trocr-es` | `ifesther/trocr-spanish-handwritten` (modern handwriting, synthetic training) on the same lines. | same |
| PaddleOCR 3 | `paddle` | PP-OCRv5 detection plus the PP-OCRv6 Latin-script recogniser. | CPU |
| docTR | `doctr` | DBNet plus PARSeq, pretrained on printed documents. | CPU, Apple GPU |
| Surya OCR 2 | `surya` | A small vision-language model that reads a whole page per call, through llama.cpp on a Mac or CPU box and vLLM on NVIDIA. Its weights carry Datalab's own licence (free for personal and small-company use). | Apple GPU, CUDA |
| LLM reading | `llm-opus` (any name) | `engines/llm_cli.sh` asks a CLI agent (Claude Code headless by default) to transcribe each page cold, with no context. | API |
| Any other transcription | any folder | FamilySearch's automatic transcription, a volunteer's, your own: drop `<page id>.txt` files into a folder under `OUT` and it is scored like the rest. | |

Transkribus is not included: its public models only run inside a Transkribus account (web app or API with credits). There is no way to run them locally or anonymously.

## Quick start

On the machine that will do the work (it needs about 15 GB free and should not be the laptop you are typing on):

```sh
export BENCH_HOME=/srv/scratch/ocr-bench      # every venv, model and cache goes here
./setup.sh                                    # or ./setup.sh tesseract kraken
brew install llama.cpp                        # Surya on a Mac; on Linux build llama.cpp or use vLLM
samples/fetch.sh
python3 stage.py --refs samples/ref --root samples --out samples/pages-staged
./run.sh samples/pages-staged samples/out
engines/llm_cli.sh samples/out/llm-opus samples/pages-staged/*     # optional
pip install edlib && python3 score.py --refs samples/ref --outputs samples/out
```

`run.sh` skips pages that already have output, so an interrupted run resumes. `rm -rf "$BENCH_HOME"` removes everything setup downloaded.

## Reference files

One `<page id>.txt` per page, a few header lines and then the text:

```text
# image: pages/gaceta-1885-01-01-p3.png
# type: print-gazette
# scope: partial, one edict of a full three-column page
# source: where the transcription comes from
# names: Gregorio Vieito y Hoyos; Antonia Hernández Berrocal; Peñanalba; Salamanca
MADRID—LATINA
D. Gregorio Vieito y Hoyos, Juez de instrucción del distrito …
```

- `type` groups pages in the tables. Use whatever split helps you decide (by century, by kind of record).
- A reference does not need to cover the whole page. Transcribe the entry you care about; mark gaps with `…`. Square brackets are editorial (`Fran[cis]co`) and are dropped before scoring, so write expansions in brackets and the page's own letters outside them.
- `names` lists the person and place names as the page writes them, separated by `;`.

## How the scores are computed

Both sides are normalised first: lower case, accents and punctuation removed, bracketed text dropped. Then:

- The reference is cut at every `…` into segments. Each segment is aligned against the best-matching stretch of the engine output (an infix alignment with edlib). So a reference covering one baptism on a page of six is not charged for the other five, and an engine that reads the right-hand page before the left is not charged for the order either.
- **CER** is the sum of character edits over the sum of reference characters, per document type. **WER** is the same over words.
- **names found**: a name counts as found when it appears in the output with at most one edit per six letters (`marz garcia` matches `mrz garcia`; `pujol` must be exact).

Read the CER of a weak engine with care. Because each segment gets the best-matching stretch of the output, unrelated text still scores around 60-75% CER, never 100%. In practice, anything above 50% means the engine did not read the page. Score an engine's output against the wrong pages once to see the floor for your own set.

## Using it to pick pages: `--spot`

The question a bench should answer for a research workflow is narrower than "how good is the text": given a book of 400 downloaded pages and the surnames you are after, which pages does someone need to read? `score.py --spot` measures exactly that. Every name word of five or more letters from all references is searched in every page's output with the same fuzzy match as **names found**, and for each threshold X the table gives two numbers:

- the share of surnames that are on the page and score at least X (how many real hits the triage keeps), and
- the share of surnames that are not on the page but still score at least X (how many pages it sends you to for nothing).

"On the page" means in the reference text. With partial references, a surname written in another entry of the same page counts against the engine, so the second number is an upper bound.

The way to use the result: run the winning engine over every page of a book, compute the spot score of each target surname per page (`1 - edits / letters`, best stretch of the page), and read closely only the pages where a target scores at or above the X you picked from the table.

## Traps we hit

- **A module named `paddle.py` shadows the `paddle` package.** PaddleOCR then dies with `partially initialized module 'paddle' has no attribute 'device' (most likely due to a circular import)`. The adapter is `paddle_ocr.py` for that reason; do not rename it.
- **PaddleOCR 3 leaks memory page by page.** On a 47-page run its process grew past 12 GB, and with other work on the same 16 GB Mac, swap reached 33 GB. Restarting the run resets it (`run.sh` skips finished pages), and on a small box run it alone.
- **PaddleOCR is slow on dense print on CPU.** A full newspaper page took up to 11 minutes, against 15-20 s for a sparse handwritten page. It also downscales anything over 4000 px on its longest side (the yellow `max_side_limit` warning), which costs small print.
- **`run.sh` kills its whole process group on exit.** Two runs started from the same shell (`nohup run.sh … & nohup run.sh … &`) share one group, so stopping one kills the other. Start each run from its own ssh session or terminal.
- **Two heavy engines in parallel on 16 GB is slower than one after the other.** TrOCR fell to one page every nine minutes, at 5% CPU, while PaddleOCR held the memory. Check `memory_pressure` before adding a stream.
- **Kraken prints a Core ML warning (`You will not be able to run predict() on this Core ML model`) and a "legacy polygon extractor" warning.** Both are harmless: Kraken runs the model through PyTorch, and McCATMuS simply predates the faster extractor.
- **Surya 2 on a Mac needs `llama-server` on the PATH** (`brew install llama.cpp`); it downloads about 1.5 GB of GGUF weights on first use and starts its own server, which `run.sh` cleans up. A request can hang with the GPU idle and both processes asleep; Surya gives up after `SURYA_INFERENCE_TIMEOUT_SECONDS` (600 s by default) and retries, so a stall under ten minutes is not a dead run.
- **TrOCR reads lines, not pages.** It needs Kraken's segmentation first (`trocr-*` read `OUT/kraken/seg`), and its time grows with the number of lines: a dense printed page took over 7 minutes. Both TrOCR models are handwriting models, so skip printed pages.
- **edlib accepts at most 256 distinct symbols.** Word-level alignment of a long page has more distinct words than that; `score.py` maps every output word that is not in the reference to one shared symbol, and falls back to plain dynamic programming above 255 reference words.
- **CER never reaches 100%.** Because each reference segment gets the best-matching stretch of the output, unrelated text scores about 60-75% CER. Read anything above 50% as "did not read the page".
- **The reference decides the LLM score.** If the reference transcriptions were themselves made by an LLM reading the page (common when a research agent wrote them), the LLM engine is scored against a reader like itself and its score is optimistic. Check a few references against the image by eye before trusting a small gap.

## Picking the box

Everything except the LLM reading is local. On an Apple Silicon Mac mini (M4, 16 GB, shared with other work) a 47-page run took, per page: Tesseract 1-15 s, docTR 1-9 s, Kraken 20 s alone and up to several minutes under memory pressure, PaddleOCR 15 s on a sparse handwritten page and 3-11 minutes on a dense printed one, TrOCR about 60 s per handwritten page and over 7 minutes on a printed one, Surya 30 s to 9 minutes (one request hung until Surya's 600 s timeout and was retried). The LLM reading took about 45 s per page through `claude -p`, three pages at a time. Run on a server or a spare machine and kill what you start: `run.sh` kills its whole process group on exit, including the llama.cpp server Surya starts.
