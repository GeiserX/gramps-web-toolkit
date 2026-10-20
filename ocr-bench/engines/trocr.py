"""TrOCR (or any Hugging Face VisionEncoderDecoder line model) on the lines Kraken found.

TrOCR reads one text line at a time, so it needs a segmenter. Run kraken_htr.py first and
pass its seg/ folder with --seg; each line is cut out along its polygon on a white ground.
"""
import json
import os

from PIL import Image, ImageDraw

from _common import page_id, run


def load(args):
    import torch
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    proc = TrOCRProcessor.from_pretrained(args.model)
    model = VisionEncoderDecoderModel.from_pretrained(args.model).to(args.device).eval()
    return proc, model, torch


def line_crops(im, lines):
    for line in lines:
        poly = [tuple(p) for p in line["boundary"]]
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        box = (max(0, min(xs)), max(0, min(ys)), min(im.width, max(xs)), min(im.height, max(ys)))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            continue
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).polygon(poly, fill=255)
        white = Image.new("RGB", im.size, "white")
        yield Image.composite(im, white, mask).crop(box)


def read(model, path, args):
    proc, net, torch = model
    with open(os.path.join(args.seg, page_id(path) + ".json")) as f:
        lines = json.load(f)
    im = Image.open(path).convert("RGB")
    crops, out = list(line_crops(im, lines)), []
    for i in range(0, len(crops), args.batch):
        px = proc(images=crops[i:i + args.batch], return_tensors="pt").pixel_values.to(args.device)
        with torch.no_grad():
            ids = net.generate(px, max_new_tokens=96, num_beams=args.beams)
        out += proc.batch_decode(ids, skip_special_tokens=True)
    return "\n".join(out)


run(read, load, extra_args=[
    (["--model"], {"required": True}),
    (["--seg"], {"required": True, "help": "kraken_htr.py output seg/ folder"}),
    (["--device"], {"default": "cpu"}),
    (["--batch"], {"type": int, "default": 16}),
    (["--beams"], {"type": int, "default": 1}),
])
