"""Kraken: baseline segmentation (blla) plus a recognition model, here McCATMuS.

Also writes the line polygons to <out>/seg/<page id>.json so the TrOCR adapter can reuse
the same lines instead of segmenting again.
"""
import json
import os

from PIL import Image

from _common import run


def load(args):
    from kraken.lib import vgsl, models
    seg = vgsl.TorchVGSLModel.load_model(args.seg_model) if args.seg_model else None
    rec = models.load_any(args.model, device=args.device)
    return seg, rec


def read(model, path, args):
    from kraken import blla, rpred
    seg_model, rec = model
    im = Image.open(path)
    kw = {"device": args.device}
    if seg_model is not None:
        kw["model"] = seg_model
    seg = blla.segment(im, **kw)
    os.makedirs(os.path.join(args.out, "seg"), exist_ok=True)
    with open(os.path.join(args.out, "seg", os.path.splitext(os.path.basename(path))[0] + ".json"), "w") as f:
        json.dump([{"baseline": l.baseline, "boundary": l.boundary} for l in seg.lines], f)
    return "\n".join(r.prediction for r in rpred.rpred(rec, im, seg))


run(read, load, extra_args=[
    (["--model"], {"required": True}),
    (["--seg-model"], {"default": None}),
    (["--device"], {"default": "cpu"}),
])
