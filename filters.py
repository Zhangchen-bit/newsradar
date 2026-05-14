"""Shared noise filters for news items.

Patterns identified from real data on 2026-05-14:
  * jin10 promo / sponsored: 【期货盯盘神器专属文章】, 【期货热点追踪】, etc.
  * cls/wscn editorial roundups: 【XX 新闻精选】, multi-numbered summaries.

Filtering at the export/clustering layer means these never reach either the
public news.json nor the local SQLite-fed UI summary. The raw rows still
land in SQLite if you run the local pipeline (good for backfill/inspection),
but the feed and BYOK exports drop them.
"""
from __future__ import annotations

import re

# ---- Promo / sponsored content from jin10 ----------------------------------
# jin10 wraps its sponsored 'expert column' / 'tool ad' content in distinctive
# brackets. These look like flash news but are paid placements.
_PROMO_BRACKET_PATTERNS = [
    "期货盯盘神器", "期货热点追踪", "金十专栏", "金十数据观察",
    "专属文章", "付费文章", "盯盘神器", "闪牛分析", "掘金者",
    "金十研究院", "盘前必读", "盘后必读", "晚间精读",
]
_PROMO_RE = re.compile(
    r"^【(?:[^】]*?)(?:" + "|".join(_PROMO_BRACKET_PATTERNS) + r")(?:[^】]*?)】"
)

# ---- Editorial roundups (multi-event summaries) ----------------------------
# These are templated daily digests, not single events. Carry many items
# but break the assumption "one cluster = one event", and confuse entity
# extraction in verifier.py.
_SUMMARY_TITLE_RE = re.compile(
    r"(新闻精选|晚间精选|盘点|新闻汇总|要闻速递|早间财经|今日精选|要闻精选|"
    r"晨会精要|晚报|盘前精选|盘后精选|每日要闻)"
)
# Use Chinese 、 (ideographic comma) only — list items go "1、xxx 2、xxx".
# Decimal numbers like "0.9%" or "157.81" use ASCII "." and would false-trigger
# if we matched the dot.
_NUMBERED_LIST_RE = re.compile(r"(?:^|[\n\s])\d{1,2}、")


def is_promo(content: str) -> bool:
    """True if the content looks like jin10's sponsored/promo bracket."""
    if not content:
        return False
    return bool(_PROMO_RE.match(content.strip()))


def is_summary_roundup(content: str) -> bool:
    """True if the content is an editorial multi-event roundup post."""
    if not content:
        return False
    if _SUMMARY_TITLE_RE.search(content):
        return True
    # 3 or more numbered items implies a digest list
    if len(_NUMBERED_LIST_RE.findall(content)) >= 3:
        return True
    return False


def is_noise(content: str) -> tuple[bool, str]:
    """Combined gatekeeper. Returns (drop, reason).

    `reason` is a short tag for logging / source_status reporting:
      - 'promo'   — jin10 sponsored bracket
      - 'roundup' — editorial multi-event summary
      - ''        — keep
    """
    if is_promo(content):
        return True, "promo"
    if is_summary_roundup(content):
        return True, "roundup"
    return False, ""


if __name__ == "__main__":
    # quick self-test against the examples cccc flagged
    samples = [
        ("【期货盯盘神器专属文章】CBOT农产品晚间分析：美豆期货价格下跌", True, "promo"),
        ("【期货热点追踪】CBOT大豆期货价格上涨，受美豆乐观出口预期", True, "promo"),
        ("【财联社5月14日晚间新闻精选】1、央行发布；2、汽协公布；3、中芯", True, "roundup"),
        ("习近平同美国总统特朗普举行会谈。", False, ""),
        ("现货白银失守84美元/盎司，日内跌超4%。", False, ""),
        ("【深圳市创想三维科技股份有限公司通过港交所上市聆讯】财联社5月11日电", False, ""),
        # regression: decimals shouldn't be mistaken for numbered list items
        ("美国3月商业库存环比 0.9%，预期 0.9%，前值 0.4%。", False, ""),
        ("英伟达股价上涨 3.01%，报 232.63 美元/股，总市值 5.63 万亿美元。", False, ""),
    ]
    for text, expect_drop, expect_reason in samples:
        drop, reason = is_noise(text)
        ok = (drop == expect_drop) and (reason == expect_reason)
        flag = "✓" if ok else "✗"
        print(f"  {flag} drop={drop:5} reason={reason:8s} | {text[:50]}")
