"""
合成测试样本：用 reportlab 生成可复现的典型中文工程 PDF，避免把真实（可能涉密的）文件放进仓库。

可单独运行生成到目录，方便人工查看：
    python tests/samples.py out_dir/
"""

from __future__ import annotations

import io
import os
import random
import sys

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A1, A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))

# 刻意包含大量"设计/专业/阶段/审核/日期/比例/审定"——旧版 classify 会把它误判为图纸
SPEC_PARA = (
    "3.1.2 车辆基地的设计应符合城市总体规划及线网规划的要求，各专业设计应在初步设计阶段完成方案审核；"
    "施工图设计文件的审定日期应在报审前确定，检修库的建筑比例、工程名称及图纸编号应统一。"
    "3.1.3 停车列检库宜与洗车库、镟轮库统筹布置，并应满足运营、检修和消防的要求。"
)


def _wrap(text: str, width: int) -> list[str]:
    return [text[i:i + width] for i in range(0, len(text), width)]


def _spec_page(c: canvas.Canvas, seed: int = 0) -> None:
    c.setFont(FONT, 10.5)
    y = 800
    lines = _wrap(SPEC_PARA * 6, 46)
    for line in lines[seed % 3: seed % 3 + 30]:
        c.drawString(48, y, line)
        y -= 24


def _scan_image(w: int, h: int, seed: int = 0) -> ImageReader:
    rnd = random.Random(seed)
    img = Image.new("L", (w, h), 245)
    d = ImageDraw.Draw(img)
    for row in range(60, h - 60, 28):
        x = 50
        while x < w - 80:
            seg = rnd.randint(20, 90)
            d.rectangle([x, row, x + seg, row + 12], fill=rnd.randint(20, 80))
            x += seg + rnd.randint(8, 20)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def _scan_page(c: canvas.Canvas, seed: int = 0, ocr_text: bool = False) -> None:
    w, h = A4
    c.drawImage(_scan_image(620, 877, seed), 0, 0, width=w, height=h)
    if ocr_text:
        t = c.beginText(48, 800)
        t.setFont(FONT, 10.5)
        t.setTextRenderMode(3)  # 隐形文字层，模拟扫描件 OCR 结果
        for line in _wrap(SPEC_PARA * 3, 46)[:20]:
            t.textLine(line)
        c.drawText(t)


def _drawing_content(c: canvas.Canvas, w: float, h: float, seed: int = 0) -> None:
    rnd = random.Random(seed)
    c.setLineWidth(0.3)
    for _ in range(450):  # 矢量图面
        x, y = rnd.uniform(40, w * 0.8), rnd.uniform(40, h - 40)
        c.line(x, y, x + rnd.uniform(-200, 200), y + rnd.uniform(-200, 200))
    c.setFont(FONT, 9)
    for i in range(25):  # 图面标注
        c.drawString(rnd.uniform(60, w * 0.7), rnd.uniform(80, h - 80), f"KZ{i + 1} 3600 ±0.000")


def _title_block(c: canvas.Canvas, x0: float, y0: float) -> None:
    c.rect(x0, y0, 520, 180)
    c.setFont(FONT, 11)
    cells = [
        "建设单位 武汉某轨道交通公司", "工程名称 某车辆基地检修库", "图名 检修库一层平面图",
        "图号 JX-A-101", "比例 1:200", "设计 张三", "校核 李四", "审核 王五", "审定 赵六",
        "专业负责人 钱七", "项目负责人 孙八", "日期 2026.10", "阶段 施工图",
    ]
    for i, cell in enumerate(cells):
        c.drawString(x0 + 10 + (i % 2) * 260, y0 + 160 - (i // 2) * 24, cell)


def _drawing_page(c: canvas.Canvas, seed: int = 0) -> None:
    w, h = landscape(A1)
    c.setPageSize((w, h))
    _drawing_content(c, w, h, seed)
    _title_block(c, w - 560, 30)


def _rotated_drawing_page(c: canvas.Canvas, seed: int = 0) -> None:
    """以竖向 A1 存储、/Rotate 90 显示为横向；图签画在未旋转坐标的右上角 = 可视右下角。"""
    w, h = A1  # 竖向存储：MediaBox = [0 0 w h]，内容按未旋转坐标绘制
    c.setPageRotation(90)
    c.setPageSize((h, w))  # reportlab 对 90° 页会交换 MediaBox 宽高，这里预先反向传入
    _drawing_content(c, w, h, seed)
    c.saveState()
    c.translate(w - 30, h - 560)  # 未旋转坐标的右上角
    c.rotate(90)
    _title_block(c, 0, 0)
    c.restoreState()


def _garbled_page(c: canvas.Canvas) -> None:
    """未嵌入 CJK 字体 → 抽取得到占位方块，模拟缺失 ToUnicode 的"乱码文本层"。"""
    c.setFont("Helvetica", 10.5)
    y = 800
    for line in _wrap(SPEC_PARA * 4, 46)[:28]:
        c.drawString(48, y, line)
        y -= 24


def build_all(out_dir: str) -> dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    paths: dict[str, str] = {}

    def make(name: str, pages) -> None:
        path = os.path.join(out_dir, f"{name}.pdf")
        c = canvas.Canvas(path, pagesize=A4)
        for fn in pages:
            c.setPageSize(A4)
            fn(c)
            c.showPage()
        c.save()
        paths[name] = path

    make("spec", [lambda c, i=i: _spec_page(c, i) for i in range(6)])
    make("drawing", [lambda c, i=i: _drawing_page(c, i) for i in range(3)])
    make("drawing_rotated", [lambda c, i=i: _rotated_drawing_page(c, i) for i in range(2)])
    make("scan", [lambda c, i=i: _scan_page(c, i) for i in range(4)])
    make("scan_ocr", [lambda c, i=i: _scan_page(c, i, ocr_text=True) for i in range(3)])
    make("garbled", [_garbled_page for _ in range(3)])
    make("mixed", [
        *[lambda c, i=i: _spec_page(c, i) for i in range(3)],
        *[lambda c, i=i: _scan_page(c, i) for i in range(3)],
        lambda c: None,  # 空白页
        *[lambda c, i=i: _spec_page(c, i) for i in range(2)],
    ])
    # 封面 + 目录几乎无字，旧版只看前 5 页会误判为扫描件
    make("cover_then_spec", [
        lambda c: (c.setFont(FONT, 28), c.drawString(150, 500, "地铁车辆基地设计标准")),
        lambda c: (c.setFont(FONT, 12), c.drawString(80, 760, "目 次")),
        lambda c: None, lambda c: None, lambda c: None,
        *[lambda c, i=i: _spec_page(c, i) for i in range(10)],
    ])
    return paths


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "samples_out"
    for k, v in build_all(out).items():
        print(k, v)
