import json

import pytest

from _pdfbackend import RawPage, _unrotated_box, _visible_box
from classify import (
    classify_page,
    count_chinese_chars,
    garbled_ratio,
    inspect_pdf,
    main,
    repeated_lines,
    sample_indices,
)
from samples import SPEC_PARA


def raw(**kw):
    base = dict(index=0, width=595, height=842, text="", region_text="", path_count=0,
                image_coverage=0.0, text_objects=10, invisible_text_objects=0)
    base.update(kw)
    return RawPage(**base)


# ------------------------------------------------------------ 纯函数
def test_count_chinese_chars_not_bytes():
    s = "车辆基地 abc 123，㐀"
    assert count_chinese_chars(s) == 5
    assert len(s.encode("utf-8")) > 5 * 3


def test_garbled_ratio():
    assert garbled_ratio("正常的中文文本") == 0
    assert garbled_ratio("3.1.2 ■■■■■■■■") > 0.5
    assert garbled_ratio("(cid:12)(cid:34)ab") > 0.5


def test_sample_spreads_across_document():
    idx = sample_indices(200, 10)
    assert idx[0] == 0 and idx[-1] == 199
    assert len(idx) == 10
    assert sample_indices(30, None) == list(range(30))  # 小文档默认全量
    assert sample_indices(10, 0) == list(range(10))


def test_spec_prose_with_title_words_is_not_drawing():
    """回归：旧版命中 ≥4 个"设计/审核/日期/专业…"即判图纸，设计规范整本会被误送 OCR。"""
    text = SPEC_PARA * 8
    v = classify_page(raw(text=text, region_text=text[:200]))
    assert v.cls == "text"


def test_drawing_page_detected():
    v = classify_page(raw(width=2384, height=1684, path_count=800,
                          text="KZ1 3600\n图号 JX-101 图名 平面图 比例 1:200 审定 校核 设计 日期",
                          region_text="图号 JX-101 图名 平面图 比例 1:200 审定 校核"))
    assert v.cls == "drawing"


def test_background_image_with_visible_text_stays_text():
    v = classify_page(raw(text=SPEC_PARA * 4, image_coverage=1.0, invisible_text_objects=0))
    assert v.cls == "text"


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_unrotated_box_maps_visible_bottom_right(rot):
    w0, h0 = 1000.0, 2000.0  # 未旋转尺寸
    left, bottom, right, top = _unrotated_box(rot, (0.9, 0.9, 1.0, 1.0), 0, 0, w0, h0)
    cx, cy = (left + right) / 2, (bottom + top) / 2
    # 可视右下角对应的未旋转角点（PDF 坐标，左下原点）
    expected = {0: (w0, 0), 90: (w0, h0), 180: (0, h0), 270: (0, 0)}[rot]
    assert abs(cx - expected[0]) < w0 * 0.1 and abs(cy - expected[1]) < h0 * 0.1


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_visible_box_inverts_unrotated_box(rot):
    box = (0.1, 0.2, 0.4, 0.7)
    l_, b, r, t = _unrotated_box(rot, box, 5.0, 7.0, 1000.0, 2000.0)
    got = _visible_box(rot, (l_, b, r, t), 5.0, 7.0, 1000.0, 2000.0)
    assert all(abs(a - e) < 1e-9 for a, e in zip(got, box, strict=True))


def test_scan_with_visible_watermark_is_scan():
    """回归：扫描件上压着每页相同的可见水印（>50 字），旧版判为文本页，fast 只抽出水印。"""
    wm = "本资料仅供学习交流使用 请勿用于商业用途 某某资料共享平台\n下载地址 www.example.com 更多资料请访问本站"
    rep = repeated_lines([wm] * 5)
    assert rep
    v = classify_page(raw(text=wm, image_coverage=1.0), rep)
    assert v.cls == "scan"
    assert classify_page(raw(text=wm, image_coverage=1.0)).cls == "text"  # 不知道跨页重复时无法区分


def test_repeated_lines_needs_enough_pages():
    assert repeated_lines(["同一行文字内容"] * 2) == frozenset()


def test_slide_with_big_picture_is_text_with_figure():
    """回归：标题 + 半页效果图的 PPT 页，旧版（覆盖率阈值 0.6）整页送 OCR。"""
    v = classify_page(raw(width=960, height=540, text="原方案 效果图", image_coverage=0.6,
                          image_boxes=[(114.0, 64.0, 898.0, 505.0, 0.6)]))
    assert v.cls == "text"
    assert v.figures == [[114.0, 64.0, 898.0, 505.0, 0.6]]


def test_small_icons_are_not_figures():
    v = classify_page(raw(text=SPEC_PARA * 2, image_coverage=0.02, image_boxes=[(0, 0, 10, 10, 0.001)] * 20))
    assert v.cls == "text" and v.figures == []


def test_heavy_content_stream_without_text_is_vector_only():
    """SHX 字体的 CAD 图纸：无文本层、路径太多被跳过计数，仅凭内容流体量识别。"""
    v = classify_page(raw(width=1191, height=842, text="", path_count=0, paths_counted=False,
                          content_bytes=42_000_000, text_objects=0))
    assert v.cls == "vector_only"


def test_content_bytes_alone_does_not_make_book_page_a_drawing():
    """图文混排书籍的插图页内容流也可达 30 万字节；小幅面上不作为图纸信号。"""
    v = classify_page(raw(width=757, height=550, text=SPEC_PARA * 3, content_bytes=306_954, path_count=200))
    assert v.cls == "text"


def test_a3_size_and_heavy_content_score_as_drawing():
    v = classify_page(raw(width=1191, height=842, path_count=0, paths_counted=True, content_bytes=500_000,
                          text="KZ1 3600\n图号 S-01 图名 线路平面图 比例 1:500 审定 校核",
                          region_text="图号 S-01 图名 线路平面图 比例 1:500 审定 校核"))
    assert v.cls == "drawing"
    assert v.long_side_mm == pytest.approx(420.2, abs=0.1)


# ------------------------------------------------------------ 合成 PDF 集成测试
@pytest.mark.parametrize("name,doc_type,engine", [
    ("spec", "text_native", "opendataloader-fast"),
    ("cover_then_spec", "text_native", "opendataloader-fast"),
    ("drawing", "engineering_drawing", "mineru-layout-or-vision"),
    ("drawing_rotated", "engineering_drawing", "mineru-layout-or-vision"),
    ("scan", "scan_or_image", "mineru-ocr"),
    ("scan_ocr", "scan_with_ocr_layer", "mineru-ocr"),
    ("garbled", "garbled_text_layer", "mineru-ocr"),
    ("scan_watermark", "scan_or_image", "mineru-ocr"),
    ("slides", "text_native", "opendataloader-fast"),
    ("drawing_shx", "engineering_drawing", "mineru-layout-or-vision"),
])
def test_document_routing(samples, backend, name, doc_type, engine):
    r = inspect_pdf(samples[name], backend=backend)
    assert r["document_type"] == doc_type, r
    assert r["recommended_engine"] == engine
    assert r["backend"] == backend


def test_slides_report_figure_pages(samples, backend):
    r = inspect_pdf(samples["slides"], backend=backend, include_pages=True)
    assert r["figure_pages"] == [1, 2, 3, 4]
    fig = r["pages"][0]["figures"][0]
    x0, y0, x1, y1, share = fig
    assert 170 < x0 < 190 and 90 < y0 < 110 and 770 < x1 < 790 and 490 < y1 < 510  # 可视坐标，左上原点
    assert 0.4 < share < 0.5
    assert any("大图" in w for w in r["warnings"])


def test_rotated_drawing_title_block_located(samples, backend):
    r = inspect_pdf(samples["drawing_rotated"], backend=backend, include_pages=True)
    assert any("图签区域" in s for s in r["pages"][0]["signals"])


def test_mixed_document_segments(samples, backend):
    r = inspect_pdf(samples["mixed"], backend=backend)
    assert r["document_type"] == "mixed" and r["recommended_engine"] == "split"
    segs = [(s["start"], s["end"], s["engine"]) for s in r["segments"]]
    assert segs == [
        (1, 3, "opendataloader-fast"),
        (4, 7, "mineru-ocr"),  # 第 7 页空白并入前一页段
        (8, 9, "opendataloader-fast"),
    ]


def test_cli_batch_jsonl_and_errors(samples, tmp_path, capsys):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    code = main([samples["spec"], str(bad), "--format", "jsonl"])
    lines = [json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
    assert code == 2
    assert lines[0]["recommended_engine"] == "opendataloader-fast"
    assert "error" in lines[1]


def test_cli_missing_file_reports_error(capsys):
    assert main(["/nonexistent/x.pdf"]) == 2
    assert "error" in json.loads(capsys.readouterr().out)
