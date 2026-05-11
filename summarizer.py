"""Generate multi-topic summaries for 1h / 5h / 24h windows.

A single LLM call per window produces all 8 topics in structured JSON. We
hash the cluster_ids fed into the call; if the next pass for the same window
produces the same hash, we skip the LLM (cache hit).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from db import connect
from llm import call_json, MODEL_DEFAULT

WINDOWS = {
    "1h":  3600,
    "5h":  5 * 3600,
    "24h": 24 * 3600,
}

TOPICS = [
    "矛盾点",        # 1 — internal tensions / conflicting signals across the feed
    "多头信号",      # 2 — bullish catalysts
    "空头信号",      # 3 — bearish catalysts
    "地缘政治",      # 4
    "宏观变化",      # 5 — macro / monetary / fiscal
    "政策监管",      # 6 — domestic policy and regulatory
    "AI/人工智能",   # 7
    "半导体产业链",  # 8
]

# Per-window cap on items fed to the LLM (input cost control).
# Pro subscription has a context budget; ~80 items is safe.
INPUT_CAP = {"1h": 60, "5h": 80, "24h": 100}


@dataclass
class WindowSlice:
    window: str
    since_ts: int
    items: list[dict]      # one dict per cluster (representative)
    cluster_hash: str


def slice_window(conn, window: str) -> WindowSlice:
    """Pull deduped clusters in the time window, ordered by importance desc
    then ts desc, capped at INPUT_CAP[window]."""
    seconds = WINDOWS[window]
    since = int(time.time()) - seconds
    cap = INPUT_CAP[window]

    rows = conn.execute(
        """
        WITH cluster_agg AS (
            SELECT cluster_id,
                   MAX(importance) AS importance,
                   MAX(ts) AS latest_ts,
                   COUNT(*) AS source_count,
                   GROUP_CONCAT(DISTINCT source) AS sources
            FROM news
            WHERE cluster_id IS NOT NULL AND ts >= ?
            GROUP BY cluster_id
        ),
        representative AS (
            SELECT n.cluster_id, n.content
            FROM news n
            JOIN (
                SELECT cluster_id, MAX(LENGTH(content)) AS maxlen
                FROM news WHERE cluster_id IS NOT NULL AND ts >= ?
                GROUP BY cluster_id
            ) m ON m.cluster_id = n.cluster_id AND LENGTH(n.content) = m.maxlen
            GROUP BY n.cluster_id
        )
        SELECT a.cluster_id, a.importance, a.latest_ts,
               a.source_count, a.sources, r.content,
               v.status AS verify_status
        FROM cluster_agg a
        JOIN representative r ON r.cluster_id = a.cluster_id
        LEFT JOIN verifications v ON v.cluster_id = a.cluster_id
        ORDER BY a.importance DESC, a.latest_ts DESC
        LIMIT ?
        """,
        (since, since, cap),
    ).fetchall()

    cols = ["cluster_id", "importance", "ts", "source_count",
            "sources", "content", "verify_status"]
    items = [dict(zip(cols, r)) for r in rows]

    # cache key: sorted cluster_ids
    cids = sorted(it["cluster_id"] for it in items)
    cluster_hash = hashlib.md5(
        ",".join(map(str, cids)).encode("utf-8")
    ).hexdigest()

    return WindowSlice(window=window, since_ts=since, items=items,
                       cluster_hash=cluster_hash)


PROMPT_TEMPLATE = """\
你是金融市场雷达的合成层。给定时间窗 {window} 内的去重快讯（每行一条已去重的事件），按 8 个主题输出**结构化 JSON**摘要。

# 严格要求
1. **只输出 JSON**，不要任何前后缀、解释、寒暄、markdown 代码块标记。
2. 必须严格符合下方 schema。
3. 每个主题输出 0-5 个要点；**没有相关内容时返回空数组 `[]`**，不要硬凑。
4. 每个要点是一个对象：`{{"point": "<≤80 字的判断>", "evidence": "<引用关键快讯片段>", "cluster_ids": [<相关 cluster_id 数组>]}}`。
5. 关注**信息增量**，不要复述全部快讯；偏好"对市场可能造成的反应/方向"。
6. 主题"矛盾点"专门记录快讯之间相互冲突或方向不一致的信号。
7. 主题"多头信号 / 空头信号"基于 A 股 / 港股 / 美股视角，结合宏观与公司层面的具体催化。
8. 主题"地缘政治 / 宏观变化 / 政策监管"按字面分类。
9. 主题"AI/人工智能 / 半导体产业链"专门记录这两条产业链的事件、订单、政策、技术进展。

# 输出 schema
{{
  "矛盾点":        [{{"point": "...", "evidence": "...", "cluster_ids": [..]}}],
  "多头信号":      [...],
  "空头信号":      [...],
  "地缘政治":      [...],
  "宏观变化":      [...],
  "政策监管":      [...],
  "AI/人工智能":   [...],
  "半导体产业链":  [...]
}}

# 时间窗：最近 {window}
# 快讯条数：{n}

# 快讯（格式：[cluster_id|importance|时间 HH:MM|sources] 内容）

{news_block}
"""


def build_news_block(items: list[dict]) -> str:
    import datetime as dt
    lines = []
    for it in items:
        t = dt.datetime.fromtimestamp(it["ts"]).strftime("%H:%M")
        imp = it["importance"]
        marker = "★" if imp >= 3 else ("·" if imp >= 2 else " ")
        verify = " [✓核实]" if it.get("verify_status") == "confirmed" else ""
        content = (it["content"] or "").replace("\n", " ").strip()[:200]
        lines.append(
            f"[{it['cluster_id']:>4}|{marker}|{t}|{it['sources']}]{verify} {content}"
        )
    return "\n".join(lines)


def summarize_window(window: str, *, force: bool = False,
                     model: str = MODEL_DEFAULT) -> dict:
    """Generate (or hit cache for) the summary for one time window.

    Returns the summary row as dict. Logs to `summaries` table.
    """
    conn = connect()
    sl = slice_window(conn, window)

    # cache: if last summary for this window has same cluster_hash, return it
    if not force:
        prev = conn.execute(
            "SELECT id, generated_at, topics_json, model FROM summaries "
            "WHERE window=? AND cluster_hash=? ORDER BY generated_at DESC LIMIT 1",
            (window, sl.cluster_hash),
        ).fetchone()
        if prev:
            return {
                "id": prev[0], "window": window, "generated_at": prev[1],
                "cached": True, "cluster_count": len(sl.items),
                "topics": json.loads(prev[2]), "model": prev[3],
            }

    if not sl.items:
        # nothing to summarize; still record so the worker doesn't spin
        empty = {t: [] for t in TOPICS}
        return _save(conn, window, sl, empty, model, elapsed_ms=0,
                     input_summary="empty window")

    prompt = PROMPT_TEMPLATE.format(
        window=window, n=len(sl.items),
        news_block=build_news_block(sl.items),
    )
    parsed, elapsed_s = call_json(prompt, model=model)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected JSON object, got {type(parsed).__name__}")

    # normalize: ensure all 8 keys present
    for t in TOPICS:
        parsed.setdefault(t, [])

    return _save(conn, window, sl, parsed, model,
                 elapsed_ms=int(elapsed_s * 1000),
                 input_summary=f"{len(sl.items)} clusters")


def _save(conn, window: str, sl: WindowSlice, topics: dict,
          model: str, elapsed_ms: int, input_summary: str) -> dict:
    now = int(time.time())
    cur = conn.execute(
        """INSERT INTO summaries
           (window, generated_at, cluster_hash, cluster_count, model,
            topics_json, input_summary, elapsed_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (window, now, sl.cluster_hash, len(sl.items), model,
         json.dumps(topics, ensure_ascii=False), input_summary, elapsed_ms),
    )
    conn.commit()
    return {
        "id": cur.lastrowid, "window": window, "generated_at": now,
        "cached": False, "cluster_count": len(sl.items),
        "topics": topics, "model": model, "elapsed_ms": elapsed_ms,
    }


if __name__ == "__main__":
    import sys
    window = sys.argv[1] if len(sys.argv) > 1 else "1h"
    force = "--force" in sys.argv
    res = summarize_window(window, force=force)
    print(f"window={res['window']} clusters={res['cluster_count']} "
          f"cached={res.get('cached')} elapsed_ms={res.get('elapsed_ms', 0)}")
    print(json.dumps(res["topics"], ensure_ascii=False, indent=2))
