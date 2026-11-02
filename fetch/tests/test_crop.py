"""gwt-fetch crop: pieces beside the original, sizes, and a manifest.tsv row naming the source. Offline."""
import argparse
import hashlib
import sys

import pytest

from gwt_fetch import cli, crop
from gwt_fetch.store import Store
from test_fetch import rows

Image = pytest.importorskip("PIL.Image")


def args(images, **kw):
    base = dict(images=[str(i) for i in images], box=None, grid=None, half=None, from_size=None,
                overlap="0%", max=None)
    base.update(kw)
    return argparse.Namespace(**base)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def page(tmp_path):
    """A 4000x3000 page saved by a download, with its manifest.tsv row."""
    p = tmp_path / "0001_pag-1.png"
    Image.new("RGB", (4000, 3000), "white").save(p)
    Store(tmp_path).record(p, "pag. 1", "https://img.example/iiif/2/A/full/max/0/default.jpg",
                           "https://img.example/manifest")
    return p


def size(path):
    with Image.open(path) as im:
        return im.size


def test_box_writes_a_piece_and_a_row_naming_the_source(page):
    assert crop.run(args([page], box="100,200,1500,800")) == 0
    out = page.parent / "0001_pag-1_box-100-200-1500-800.jpg"
    assert size(out) == (1500, 800)
    row = {r["file"]: r for r in rows(page.parent / "manifest.tsv")}[out.name]
    assert row["sha256"] == sha(out)
    assert row["label"] == f"crop 100,200,1500,800 of 0001_pag-1.png sha256:{sha(page)}"
    assert row["image_url"] == "https://img.example/iiif/2/A/full/max/0/default.jpg#xywh=100,200,1500,800"
    assert row["manifest_url"] == "https://img.example/manifest"


def test_box_from_a_preview_scales_to_the_page(page):
    # measured on a 1000x750 preview: a quarter of the real size
    assert crop.run(args([page], box="25,50,500,250", from_size="1000x750")) == 0
    assert size(page.parent / "0001_pag-1_box-100-200-2000-1000.jpg") == (2000, 1000)


def test_box_past_the_edge_is_refused(page):
    with pytest.raises(SystemExit, match="reaches past"):
        crop.run(args([page], box="3000,0,1001,10"))
    assert not list(page.parent.glob("*_box-*"))


def test_grid_tiles_cover_the_page(page):
    assert crop.run(args([page], grid="2x3")) == 0
    tiles = sorted(page.parent.glob("0001_pag-1_grid2x3-*.jpg"))
    assert len(tiles) == 6
    assert {size(t) for t in tiles} == {(2000, 1000)}
    shas = {r["file"]: r["sha256"] for r in rows(page.parent / "manifest.tsv")}
    assert all(shas[t.name] == sha(t) for t in tiles)


def test_grid_overlap_reaches_into_neighbours(page):
    assert crop.run(args([page], grid="2x3", overlap="10%")) == 0
    d = page.parent
    assert size(d / "0001_pag-1_grid2x3-r1c1.jpg") == (2200, 1100)   # corner: grows on two sides
    assert size(d / "0001_pag-1_grid2x3-r2c2.jpg") == (2200, 1200)   # middle row: grows up and down
    url = {r["file"]: r["image_url"] for r in rows(d / "manifest.tsv")}["0001_pag-1_grid2x3-r2c2.jpg"]
    assert url.endswith("#xywh=1800,900,2200,1200")


def test_half_with_max(page):
    assert crop.run(args([page], half="right", max=1600)) == 0
    out = page.parent / "0001_pag-1_right.jpg"
    assert size(out) == (1067, 1600)  # longest side 3000 -> 1600
    row = {r["file"]: r for r in rows(page.parent / "manifest.tsv")}[out.name]
    assert row["image_url"].endswith("#xywh=2000,0,2000,3000")  # the box stays in source pixels


def test_rerun_never_overwrites(page):
    crop.run(args([page], half="left"))
    out = page.parent / "0001_pag-1_left.jpg"
    before = out.stat().st_mtime_ns
    crop.run(args([page], half="left"))
    assert out.stat().st_mtime_ns == before
    assert [r["file"] for r in rows(page.parent / "manifest.tsv")].count(out.name) == 1


def test_image_without_a_row_uses_its_file_name(tmp_path):
    p = tmp_path / "scan.jpg"
    Image.new("L", (300, 200)).save(p)
    assert crop.run(args([p], half="left")) == 0
    row = rows(tmp_path / "manifest.tsv")[0]
    assert row["image_url"] == "scan.jpg#xywh=0,0,150,200" and row["manifest_url"] == ""


def test_without_pillow_exits_2(page, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "PIL", None)
    assert crop.run(args([page], half="left")) == 2
    assert "needs Pillow" in capsys.readouterr().err


def test_cli_wires_crop(page):
    assert cli.main(["crop", str(page), "--grid", "1x2"]) == 0
    assert len(list(page.parent.glob("0001_pag-1_grid1x2-*.jpg"))) == 2
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["crop", str(page)])  # one of --box, --grid, --half is required


def test_box_to_the_preview_edge_stays_inside_the_page(tmp_path):
    # 150+750 ends at the preview's right edge; scaling x and w apart rounded to 822+4108 = 4930 > 4929
    p = tmp_path / "wide.png"
    Image.new("L", (4929, 600)).save(p)
    assert crop.run(args([p], box="150,0,750,110", from_size="900x110")) == 0
    out = tmp_path / "wide_box-822-0-4107-600.jpg"
    assert size(out) == (4107, 600)
