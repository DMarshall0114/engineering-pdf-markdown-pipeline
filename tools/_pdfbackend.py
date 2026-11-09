"""
_pdfbackend.py - classify.py 使用的 PDF 读取后端抽象层

统一输出每页的原始观测量（尺寸、文本、图签区域文本、矢量路径数、内容流字节数、
图片覆盖率与图片位置、隐形文本占比），让上层判定逻辑与具体 PDF 库解耦。

后端选择顺序：环境变量 PDF_BACKEND（pymupdf / pypdfium2）> PyMuPDF > pypdfium2。
- PyMuPDF：功能最全，但为 AGPL-3.0 许可；
- pypdfium2：Apache-2.0 / BSD 许可，适合对许可证敏感的内网部署。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

# 图签（标题栏）通常位于可视页面右下角；立式图签在右侧窄条。
# 以可视页面（已考虑 /Rotate）的比例坐标定义，左上为原点。
TITLE_BLOCK_BOXES = (
    (0.55, 0.65, 1.0, 1.0),  # 右下角：x0, y0, x1, y1
    (0.85, 0.0, 1.0, 1.0),  # 右侧竖条
)

# 内容流超过该字节数时不再逐个统计矢量路径：大幅面 CAD 图纸单页可达数十万条路径，
# get_cdrawings() 要 1~4 秒/页；内容流字节数与路径数高度同向（实测 Spearman ρ≈0.98），
# 读取却快约 20 倍。此时 path_count 记为 0、paths_counted=False，由上层按"矢量很重"处理。
PATH_COUNT_SKIP_BYTES = 4_000_000


class BackendUnavailable(RuntimeError):
    pass


class EncryptedPDF(RuntimeError):
    pass


@dataclass
class RawPage:
    index: int  # 0-based
    width: float  # 可视宽度 (pt)
    height: float  # 可视高度 (pt)
    text: str
    region_text: str  # 图签候选区域内的文本
    path_count: int  # 矢量路径对象数（含表单 XObject 内）
    image_coverage: float  # 图片覆盖页面面积比例，0~1（多图叠加时截断为 1）
    text_objects: int
    invisible_text_objects: int  # 渲染模式 3（隐形）文本对象数，OCR 扫描件的特征
    # 以下字段 v0.3 新增，带默认值以保持向后兼容
    content_bytes: int = 0  # 页面内容流字节数（解压后）；后端无法提供时为 0
    paths_counted: bool = True  # False 表示内容流过大、跳过了路径计数
    # 页内图片外接框：(x0, y0, x1, y1, 面积占比)，可视页面坐标（已考虑 /Rotate），左上原点，单位 pt
    image_boxes: list[tuple[float, float, float, float, float]] = field(default_factory=list)


class PdfDoc(Protocol):
    backend: str
    page_count: int

    def page(self, index: int) -> RawPage: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------- PyMuPDF
class _MuPdfDoc:
    backend = "pymupdf"

    def __init__(self, path: str):
        try:
            import pymupdf as fitz  # type: ignore  # PyMuPDF ≥1.24 推荐的导入名
        except ImportError:
            import fitz  # type: ignore

        self._fitz = fitz
        try:
            self._doc = fitz.open(path)
        except Exception as e:  # noqa: BLE001 - 透传底层错误信息
            raise RuntimeError(f"无法打开 PDF: {e}") from e
        if self._doc.needs_pass:
            self._doc.close()
            raise EncryptedPDF("PDF 已加密，需要密码")
        self.page_count = len(self._doc)

    def page(self, index: int) -> RawPage:
        fitz = self._fitz
        page = self._doc[index]
        r = page.rect  # 已考虑旋转的可视矩形，文本坐标与之同一坐标系
        text = page.get_text("text") or ""
        region_parts = []
        for bx0, by0, bx1, by1 in TITLE_BLOCK_BOXES:
            clip = fitz.Rect(
                r.x0 + r.width * bx0, r.y0 + r.height * by0,
                r.x0 + r.width * bx1, r.y0 + r.height * by1,
            )
            region_parts.append(page.get_text("text", clip=clip) or "")

        try:
            content_bytes = len(page.read_contents() or b"")
        except Exception:  # noqa: BLE001
            content_bytes = 0

        paths_counted = content_bytes < PATH_COUNT_SKIP_BYTES
        path_count = 0
        if paths_counted:
            get_draw = getattr(page, "get_cdrawings", None) or page.get_drawings
            try:
                path_count = len(get_draw())
            except Exception:  # noqa: BLE001 - 个别损坏页面不应中断整体探测
                path_count = 0

        page_area = max(r.width * r.height, 1.0)
        img_area = 0.0
        boxes: list[tuple[float, float, float, float, float]] = []
        for info in page.get_image_info():
            bbox = fitz.Rect(info["bbox"]) & r
            if not bbox.is_empty:
                area = bbox.width * bbox.height
                img_area += area
                boxes.append((round(bbox.x0 - r.x0, 1), round(bbox.y0 - r.y0, 1),
                              round(bbox.x1 - r.x0, 1), round(bbox.y1 - r.y0, 1), round(area / page_area, 3)))

        text_objs = invisible = 0
        try:
            for span in page.get_texttrace():
                text_objs += 1
                if span.get("type") == 3 or span.get("opacity", 1) == 0:
                    invisible += 1
        except Exception:  # noqa: BLE001 - 旧版本 PyMuPDF 无 get_texttrace
            pass

        return RawPage(
            index=index, width=r.width, height=r.height, text=text,
            region_text="\n".join(region_parts), path_count=path_count,
            image_coverage=min(img_area / page_area, 1.0),
            text_objects=text_objs, invisible_text_objects=invisible,
            content_bytes=content_bytes, paths_counted=paths_counted, image_boxes=boxes,
        )

    def close(self) -> None:
        self._doc.close()


# ---------------------------------------------------------------- pypdfium2
def _unrotated_box(rot: int, box, x_off: float, y_off: float, w0: float, h0: float):
    """把可视页面上的比例框 (x0,y0,x1,y1，左上原点) 映射到未旋转 PDF 坐标 (left,bottom,right,top)。"""
    x0, y0, x1, y1 = box
    if rot == 90:  # 顺时针 90°：可视 x ← 未旋转 y；可视自上而下 ← 未旋转 x
        return x_off + w0 * y0, y_off + h0 * x0, x_off + w0 * y1, y_off + h0 * x1
    if rot == 180:
        return x_off + w0 * (1 - x1), y_off + h0 * y0, x_off + w0 * (1 - x0), y_off + h0 * y1
    if rot == 270:
        return x_off + w0 * (1 - y1), y_off + h0 * (1 - x1), x_off + w0 * (1 - y0), y_off + h0 * (1 - x0)
    return x_off + w0 * x0, y_off + h0 * (1 - y1), x_off + w0 * x1, y_off + h0 * (1 - y0)


def _visible_box(rot: int, bounds, x_off: float, y_off: float, w0: float, h0: float):
    """_unrotated_box 的逆映射：未旋转 PDF 坐标 (left,bottom,right,top) → 可视页面比例框 (x0,y0,x1,y1，左上原点)。"""
    left, bottom, right, top = bounds

    def frac(x: float, y: float) -> tuple[float, float]:
        u, v = (x - x_off) / w0, (y - y_off) / h0  # 未旋转页面内的比例坐标，左下原点
        if rot == 90:
            return v, u
        if rot == 180:
            return 1 - u, v
        if rot == 270:
            return 1 - v, 1 - u
        return u, 1 - v

    (ax, ay), (bx, by) = frac(left, bottom), frac(right, top)
    return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


class _PdfiumDoc:
    backend = "pypdfium2"

    def __init__(self, path: str):
        import pypdfium2 as pdfium  # type: ignore
        import pypdfium2.raw as raw  # type: ignore

        self._raw = raw
        try:
            self._doc = pdfium.PdfDocument(path)
        except pdfium.PdfiumError as e:
            if "password" in str(e).lower():
                raise EncryptedPDF("PDF 已加密，需要密码") from e
            raise RuntimeError(f"无法打开 PDF: {e}") from e
        self.page_count = len(self._doc)

    def page(self, index: int) -> RawPage:
        raw = self._raw
        page = self._doc[index]
        try:
            rot = page.get_rotation() % 360
            mb_l, mb_b, mb_r, mb_t = page.get_mediabox()
            w0, h0 = mb_r - mb_l, mb_t - mb_b
            vis_w, vis_h = (h0, w0) if rot in (90, 270) else (w0, h0)

            tp = page.get_textpage()
            text = tp.get_text_range() or ""
            region_parts = []
            for box in TITLE_BLOCK_BOXES:
                left, bottom, right, top = _unrotated_box(rot, box, mb_l, mb_b, w0, h0)
                region_parts.append(tp.get_text_bounded(left, bottom, right, top) or "")
            tp.close()

            page_area = max(w0 * h0, 1.0)
            img_area = 0.0
            boxes: list[tuple[float, float, float, float, float]] = []
            paths = text_objs = invisible = 0
            for obj in page.get_objects():
                if obj.type == raw.FPDF_PAGEOBJ_PATH:
                    paths += 1
                elif obj.type == raw.FPDF_PAGEOBJ_IMAGE:
                    ol, ob, orr, ot = obj.get_bounds()
                    cl, cb, cr, ct = max(ol, mb_l), max(ob, mb_b), min(orr, mb_r), min(ot, mb_t)
                    iw, ih = max(0.0, cr - cl), max(0.0, ct - cb)
                    if iw * ih > 0:
                        img_area += iw * ih
                        fx0, fy0, fx1, fy1 = _visible_box(rot, (cl, cb, cr, ct), mb_l, mb_b, w0, h0)
                        boxes.append((round(fx0 * vis_w, 1), round(fy0 * vis_h, 1),
                                      round(fx1 * vis_w, 1), round(fy1 * vis_h, 1), round(iw * ih / page_area, 3)))
                elif obj.type == raw.FPDF_PAGEOBJ_TEXT:
                    text_objs += 1
                    if raw.FPDFTextObj_GetTextRenderMode(obj.raw) == raw.FPDF_TEXTRENDERMODE_INVISIBLE:
                        invisible += 1
        finally:
            page.close()

        return RawPage(
            index=index, width=vis_w, height=vis_h, text=text.replace("\r\n", "\n"),
            region_text="\n".join(region_parts), path_count=paths,
            image_coverage=min(img_area / page_area, 1.0),
            text_objects=text_objs, invisible_text_objects=invisible,
            content_bytes=0,  # pypdfium2 无直接读取内容流的 API；路径数已逐个统计，不影响判定
            image_boxes=boxes,
        )

    def close(self) -> None:
        self._doc.close()


_BACKENDS = {"pymupdf": _MuPdfDoc, "pypdfium2": _PdfiumDoc}
_MODULES = {"pymupdf": ("pymupdf", "fitz"), "pypdfium2": ("pypdfium2",)}


def available_backends() -> list[str]:
    import importlib.util

    found = []
    for name, mods in _MODULES.items():
        if any(importlib.util.find_spec(m) for m in mods):
            found.append(name)
    return found


def open_pdf(path: str, backend: str | None = None) -> PdfDoc:
    backend = backend or os.environ.get("PDF_BACKEND") or None
    candidates = [backend] if backend else ["pymupdf", "pypdfium2"]
    avail = available_backends()
    for name in candidates:
        if name not in _BACKENDS:
            raise BackendUnavailable(f"未知后端: {name}（可选 pymupdf / pypdfium2）")
        if name in avail:
            return _BACKENDS[name](path)
    raise BackendUnavailable(
        "未找到可用的 PDF 后端，请安装其一：pip install pymupdf  或  pip install pypdfium2"
    )


def iter_pages(doc: PdfDoc, indices) -> Iterator[RawPage]:
    for i in indices:
        yield doc.page(i)
