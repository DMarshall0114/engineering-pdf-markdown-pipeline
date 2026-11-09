from datetime import datetime, timedelta, timezone

from add_frontmatter import build_frontmatter
from clean_transcript import clean_text, clean_transcript_text, is_watermark_line
from qa_check import BUILTIN_RULES, apply_fixes, check_clauses, compare_numbers, scan, scan_forbidden, text_stats
from rebuild_headings import rebuild_headings


# ------------------------------------------------------------ clean_transcript
def test_watermark_variants_removed():
    for line in [
        "张工(012345)某某公司 2026-10-25 10:35:18",
        "王工（098765）某工程咨询有限公司 2026/11/04 14:00",
        "李四(1234) 2026年11月4日 9:05",
    ]:
        assert is_watermark_line(line), line


def test_content_line_with_number_and_time_is_kept():
    """回归：旧正则会把带 (1) 和时间戳的正文整行删掉，丢失 98.6 亿元。"""
    line = "(1) 2026-11-04 14:00:00 召开评审会，确定车站规模为12 座，投资不超过98.6亿元。"
    text, rep = clean_text(line)
    assert text == line and rep.watermarks_removed == 0


def test_spaces_removed_within_line_only():
    text, _ = clean_text("第三章\n总则内容 如 下")
    assert text == "第三章\n总则内容如下"  # 旧版 \s 会吞掉换行


def test_heading_space_preserved():
    assert clean_text("第一章 总则")[0] == "第一章 总则"
    assert clean_text("附录A 名词 解释")[0] == "附录A 名词解释"


def test_number_integrity_ok_after_cleaning():
    raw = "王工(098765)某公司 2026-11-04 14:00:00\n新 增 线 路 12.5 公 里，投 资 98.6 亿 元。"
    text, rep = clean_text(raw)
    assert "12.5" in text and "98.6" in text
    assert rep.numbers_ok and rep.watermarks_removed == 1


def test_reflow_joins_wrapped_clause_but_not_headings():
    raw = "3.1.2 车辆基地的设计应符合城市总\n体规划的要求。\n1 总则\n为规范设计，制定本标准。"
    text, rep = clean_text(raw, reflow_lines=True)
    assert text == "3.1.2 车辆基地的设计应符合城市总体规划的要求。\n1 总则\n为规范设计，制定本标准。"
    assert rep.lines_reflowed == 1


def test_drop_repeated_header():
    raw = "\n".join(["XX 地铁项目 内部资料", "正文一。", "XX 地铁项目 内部资料", "正文二。",
                     "XX 地铁项目 内部资料"])
    text, rep = clean_text(raw, drop_repeated=3)
    assert "内部资料" not in text and rep.repeated_lines_removed == 3


def test_v1_compatible_api():
    text, stats = clean_transcript_text("该 技 术 规 格 书")
    assert text == "该技术规格书" and stats["unwanted_spaces_removed"] == 5


# ------------------------------------------------------------ rebuild_headings
def test_rebuild_heading_levels():
    src = "\n".join([
        "1 总则",
        "- 1.0.1 为使地铁工程设计符合安全可靠的要求，制定本标准。",
        "2 术语",
        "3 供电",
        "3.1 一般规定",
        "3.1.1 供电系统应包括：",
        "1 电压等级",
        "3.1 一般规定 ……………… 12",
        "附录A 名词解释",
        "```",
        "1 总则",
        "```",
    ])
    out, _ = rebuild_headings(src)
    lines = out.split("\n")
    assert lines[0] == "## 1 总则"
    assert lines[1].startswith("**1.0.1** 为使")
    assert lines[4] == "### 3.1 一般规定"
    assert lines[5].startswith("**3.1.1**")
    assert lines[6] == "1 电压等级"  # 条文内款项，不能升为章
    assert lines[7] == "3.1 一般规定 ……………… 12"  # 目录行不动
    assert lines[8] == "## 附录A 名词解释"
    assert lines[10] == "1 总则"  # 代码块不动


# ------------------------------------------------------------ qa_check
def test_stats_distinguish_bytes_and_chars():
    st = text_stats("车辆基地 ABC")
    assert st["chinese_chars"] == 4 and st["utf8_bytes"] == 4 * 3 + 4


def test_confusables_fix_without_false_positive():
    text = "3.1 般规定\n主娈电所设于出人口附近，外出人口另计，已检査。"
    fixed, n = apply_fixes(text, BUILTIN_RULES)
    assert fixed == "3.1 一般规定\n主变电所设于出入口附近，外出人口另计，已检查。"
    assert n == 4
    assert all(h["match"] != "般规定" for h in scan("一般规定", BUILTIN_RULES))


def test_compare_numbers():
    r = compare_numbers("投资 98.6 亿元", "投资 98.6 亿元，里程 12.5 km")
    assert r["missing_vs_reference"] == ["12.5"] and r["extra_vs_reference"] == []


# ------------------------------------------------------------ add_frontmatter
def test_frontmatter_merges_and_keeps_user_fields(tmp_path):
    pdf = tmp_path / "规范.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")
    md = '---\ntitle: old\ntags:\n  - 规范\n---\n# 地铁设计标准\n正文"引号"'
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    out = build_frontmatter(md, str(pdf), None,
                            {"total_pages": 45, "document_type": "text_native",
                             "recommended_engine": "opendataloader-fast"}, now=now)
    head = out.split("---\n")[1]
    assert 'title: "地铁设计标准"' in head and "title: old" not in head
    assert "tags:\n  - 规范" in head
    assert 'engine: "opendataloader-fast"' in head and "source_pages: 45" in head
    assert 'converted_at: "2026-11-04T12:00:00+08:00"' in head
    assert "source_sha256:" in head
    # 幂等：再跑一次不会叠加
    again = build_frontmatter(out, str(pdf), None, None, now=now)
    assert again.count("---\n") == 2 and again.count("tags:") == 1


# ------------------------------------------------------------ qa_check 新增：禁用串 / 条文连续性
def test_forbidden_patterns_found_with_line_numbers():
    hits = scan_forbidden("3.2.11 正确\n池（箱）周围Zm内\n标准号 CJ/T 521一2018", ["Zm内", "521一", "3.2.n"])
    assert [(h["line"], h["match"]) for h in hits] == [(2, "Zm内"), (3, "521一")]


def test_clause_continuity_detects_gap_duplicate_and_disorder():
    text = "\n".join([
        "## 3.1 一般规定", "3.1.1 应符合。", "**3.1.2** 宜设置。", "3.1.4 不应采用。",  # 缺 3.1.3
        "3.2.1 甲。", "3.2.1 乙。",  # 重号
        "3.1.5 迟到的条文。",
        "| 3.9.9 | 表格行不计 |",
        "目 次 3.4.1 一般规定……………12",
        "条文说明",
        "3.1.1 说明部分重新编号，不算重号。",
    ])
    r = check_clauses(text)
    assert r["gaps"] == ["3.1.3"]
    assert r["duplicates"] == ["3.2.1 (L6)"]
    assert r["out_of_order"] == []  # 3.1.5 > 3.1.4，按节内递增仍合法
    assert r["sections"] == 2 and r["clauses"] == 7


def test_clause_out_of_order():
    r = check_clauses("3.1.2 乙\n3.1.1 甲")
    assert r["out_of_order"] == ["3.1.1 (L2)"] and r["gaps"] == ["3.1.1"]


def test_references_inside_sentences_are_not_clauses():
    r = check_clauses("应按本规范第3.2.5条执行。\n3.2.1 条文正文，见第 3.2.7 条。")
    assert r["clauses"] == 1 and r["first"] == "3.2.1"  # 句中引用不计
