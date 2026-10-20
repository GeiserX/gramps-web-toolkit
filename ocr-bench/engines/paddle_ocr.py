"""PaddleOCR 3 (PP-OCRv5 detection + the Latin-script recogniser selected by --lang)."""
from _common import lines_in_reading_order, run


def load(args):
    from paddleocr import PaddleOCR
    return PaddleOCR(lang=args.lang, use_doc_orientation_classify=False,
                     use_doc_unwarping=False, use_textline_orientation=False)


def read(ocr, path, args):
    items = []
    for res in ocr.predict(path):
        for box, text in zip(res["rec_boxes"], res["rec_texts"]):
            x0, y0, x1, y1 = (float(v) for v in box)
            items.append((x0, y0, x1, y1, text))
    return lines_in_reading_order(items)


run(read, load, extra_args=[(["--lang"], {"default": "es"})])
