"""docTR: DBNet detection + PARSeq recognition, pretrained on printed documents.

Lines are re-ordered column by column; docTR's own render() runs across columns.
"""
from _common import lines_in_reading_order, run


def load(args):
    from doctr.models import ocr_predictor
    model = ocr_predictor(det_arch=args.det, reco_arch=args.reco, pretrained=True,
                          assume_straight_pages=True)
    if args.device != "cpu":
        model = model.to(args.device)
    return model


def read(model, path, args):
    from doctr.io import DocumentFile
    page = model(DocumentFile.from_images(path)).export()["pages"][0]
    items = []
    for block in page["blocks"]:
        for line in block["lines"]:
            (x0, y0), (x1, y1) = line["geometry"]
            items.append((x0, y0, x1, y1, " ".join(w["value"] for w in line["words"])))
    return lines_in_reading_order(items)


run(read, load, extra_args=[
    (["--det"], {"default": "db_resnet50"}),
    (["--reco"], {"default": "parseq"}),
    (["--device"], {"default": "cpu"}),
])
