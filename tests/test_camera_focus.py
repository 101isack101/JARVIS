from vision.detect import parse_box_2d, box_to_pixels


def test_parse_box_2d_from_json():
    out = parse_box_2d('{"label": "multimetro", "box_2d": [100, 200, 700, 800]}')
    assert out is not None
    assert out["label"] == "multimetro"
    assert out["box_2d"] == [100, 200, 700, 800]


def test_parse_box_2d_handles_garbage():
    assert parse_box_2d("no json aqui") is None
    assert parse_box_2d('{"label": "x"}') is None  # sin box_2d


def test_box_to_pixels_denormalizes_0_1000():
    # box_2d = [ymin, xmin, ymax, xmax] en 0..1000
    px = box_to_pixels([0, 0, 1000, 1000], width=480, height=480, ox=0, oy=0)
    assert px == (0, 0, 480, 480)
    px2 = box_to_pixels([250, 250, 750, 750], width=400, height=400, ox=40, oy=40)
    assert px2 == (140, 140, 340, 340)  # 0.25*400+40 .. 0.75*400+40
