"""Surya OCR 2: a small vision-language model that reads a whole page in one call.

On a Mac or a CPU box it runs through llama.cpp (`llama-server` must be on PATH); on an
NVIDIA box Surya starts vLLM instead. The model weights carry Datalab's own licence.
"""
import html
import re

from PIL import Image

from _common import run


def load(args):
    from surya.inference import SuryaInferenceManager
    from surya.recognition import RecognitionPredictor
    manager = SuryaInferenceManager()
    return manager, RecognitionPredictor(manager)


def read(model, path, args):
    _, rec = model
    page = rec([Image.open(path).convert("RGB")], full_page=True)[0]
    blocks = sorted((b for b in page.blocks if not b.skipped), key=lambda b: b.reading_order)
    out = []
    for b in blocks:
        text = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>|</tr>", "\n", b.html)
        out.append(html.unescape(re.sub(r"<[^>]+>", "", text)).strip())
    return "\n".join(t for t in out if t)


run(read, load)
