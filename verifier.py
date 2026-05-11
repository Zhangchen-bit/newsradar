"""P2.5 — Cross-check eligible news clusters against iFinD announcements.

Eligibility: cluster importance >= 2 AND content matches an event keyword.
For each eligible cluster we extract a company entity (name or 6-digit code),
build a query, call iFinD MCP `news/search_notice`, and classify the result.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shlex
import subprocess
import time
from pathlib import Path

from db import connect

# iFinD MCP client (bundled with the skill)
IFIND_CLIENT = Path.home() / ".claude" / "skills" / "ifind-mcp-research-cn" / "scripts" / "ifind_mcp_client.js"

# ---- Trigger config ---------------------------------------------------------

EVENT_KEYWORDS = [
    "中标", "收购", "合同", "股权", "获批", "签订", "订单",
    "增持", "减持", "回购", "定增", "分红", "并购", "重大资产",
    "拟收购", "拟出售", "重组",
]

# Match between 【】 first (most reliable in cls / wscn templates)
_BRACKET_RE = re.compile(r"【([^】]{2,40})】")
# Company name endings
_COMPANY_TAIL_RE = re.compile(
    r"([一-鿿]{2,15}(?:股份有限公司|有限公司|集团股份有限公司|集团|股份|科技|实业|控股))"
)
# A-share 6-digit codes
_STOCK_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


_SUMMARY_RE = re.compile(r"(新闻精选|晚间精选|盘点|新闻汇总|要闻速递|早间财经|今日精选|要闻精选)")
_NUMBERED_ITEM_RE = re.compile(r"[\n\s]\d+[、.]")


def looks_like_summary(text: str) -> bool:
    """Detect multi-event roundup posts that aren't single-event verifiable."""
    if _SUMMARY_RE.search(text):
        return True
    if len(_NUMBERED_ITEM_RE.findall(text)) >= 3:
        return True
    return False


def find_event_keyword(text: str) -> str | None:
    for kw in EVENT_KEYWORDS:
        if kw in text:
            return kw
    return None


def extract_entity(text: str) -> tuple[str | None, str]:
    """Return (entity, kind) where kind is 'name' or 'code' or ''.

    Heuristic priority: 6-digit code > bracket-extracted company > inline company tail.
    """
    # 1) explicit code
    m = _STOCK_CODE_RE.search(text)
    if m:
        return m.group(1), "code"

    # 2) bracket content: cls/wscn often title-fy events as 【公司名 事件】
    for m in _BRACKET_RE.finditer(text):
        bracket = m.group(1)
        # Inside bracket, look for company tail
        m2 = _COMPANY_TAIL_RE.search(bracket)
        if m2:
            return m2.group(1), "name"
        # If bracket is short (looks like a label), skip; otherwise return as-is
        if 4 <= len(bracket) <= 20 and "：" not in bracket and ":" not in bracket:
            # only take if it looks company-like (contains common chars)
            if any(c in bracket for c in "公司股份集团科技实业控股"):
                return bracket.strip(), "name"

    # 3) inline company tail
    m = _COMPANY_TAIL_RE.search(text)
    if m:
        return m.group(1), "name"

    return None, ""


# ---- iFinD MCP call ---------------------------------------------------------


def call_ifind(server: str, tool: str, args: dict, timeout: int = 30) -> dict:
    if not IFIND_CLIENT.exists():
        raise FileNotFoundError(f"iFinD MCP client not found: {IFIND_CLIENT}")
    cmd = [
        "node", str(IFIND_CLIENT),
        "call",
        "--server", server,
        "--tool", tool,
        "--args-json", json.dumps(args, ensure_ascii=False),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ifind client rc={proc.returncode}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)


def _extract_inner_text(resp: dict) -> str:
    """iFinD MCP wraps the actual answer in nested JSON; pull it out."""
    try:
        outer = resp["data"]["result"]["content"][0]["text"]
        inner = json.loads(outer)
        return inner.get("data", {}).get("data") or ""
    except Exception:
        return ""


_DATE_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")


def _find_first_date(s: str) -> str:
    m = _DATE_RE.search(s)
    if not m:
        return ""
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _looks_like_hit(text_block: str, entity: str, keyword: str) -> bool:
    """Return True if the iFinD response text contains both entity and keyword."""
    if not text_block:
        return False
    # entity may be a 6-digit code or company name
    return entity in text_block and keyword in text_block


# ---- Verifier core ----------------------------------------------------------


def verify_cluster(conn, cluster_id: int, content: str, ts: int) -> dict:
    """Run verification for one cluster. Writes a row into `verifications`.

    Returns the verification row as a dict.
    """
    now = int(time.time())
    keyword = find_event_keyword(content)
    if not keyword:
        return _save_verification(conn, cluster_id, "skip",
                                  reason="no event keyword")

    entity, kind = extract_entity(content)
    if not entity:
        return _save_verification(conn, cluster_id, "skip", keyword=keyword,
                                  reason="no entity")

    # window: ±3 days around the news timestamp
    d = dt.datetime.fromtimestamp(ts).date()
    t_start = (d - dt.timedelta(days=3)).strftime("%Y-%m-%d")
    t_end = (d + dt.timedelta(days=3)).strftime("%Y-%m-%d")

    query = f"{entity} {keyword}"
    args = {
        "query": query,
        "time_start": t_start,
        "time_end": t_end,
        "size": 5,
    }

    try:
        notice_resp = call_ifind("news", "search_notice", args)
    except Exception as e:
        return _save_verification(conn, cluster_id, "error", company=entity,
                                  keyword=keyword, query=query,
                                  evidence=str(e)[:200])

    notice_text = _extract_inner_text(notice_resp)
    if _looks_like_hit(notice_text, entity, keyword):
        return _save_verification(
            conn, cluster_id, "confirmed",
            company=entity, keyword=keyword, query=query,
            evidence=notice_text[:400],
            evidence_date=_find_first_date(notice_text),
            raw=json.dumps(notice_resp, ensure_ascii=False)[:2000],
        )

    # fallback: try search_news
    try:
        time.sleep(1.0)
        news_resp = call_ifind("news", "search_news", args)
        news_text = _extract_inner_text(news_resp)
    except Exception:
        news_text = ""

    if _looks_like_hit(news_text, entity, keyword):
        return _save_verification(
            conn, cluster_id, "partial",
            company=entity, keyword=keyword, query=query,
            evidence=news_text[:400],
            evidence_date=_find_first_date(news_text),
            raw=json.dumps({"notice": notice_resp, "news": news_text[:500]},
                           ensure_ascii=False, default=str)[:2000],
        )

    return _save_verification(
        conn, cluster_id, "unconfirmed",
        company=entity, keyword=keyword, query=query,
        evidence=(notice_text[:200] or "[empty]"),
    )


def _save_verification(conn, cluster_id: int, status: str, **kw) -> dict:
    now = int(time.time())
    row = {
        "cluster_id": cluster_id,
        "status": status,
        "company": kw.get("company"),
        "keyword": kw.get("keyword"),
        "query": kw.get("query"),
        "evidence": kw.get("evidence"),
        "evidence_date": kw.get("evidence_date"),
        "raw": kw.get("raw"),
        "checked_at": now,
    }
    conn.execute(
        """INSERT OR REPLACE INTO verifications
           (cluster_id, status, company, keyword, query, evidence, evidence_date, raw, checked_at)
           VALUES (:cluster_id, :status, :company, :keyword, :query, :evidence, :evidence_date, :raw, :checked_at)""",
        row,
    )
    conn.commit()
    return row


# ---- Eligibility scan -------------------------------------------------------


def find_eligible(conn, lookback_hours: int = 12, limit: int = 20) -> list[dict]:
    """Pick clusters that look verifiable and have not been verified yet.

    The keyword filter is pushed into SQL via LIKE so LIMIT doesn't truncate
    candidates before keyword matching.
    """
    since = int(time.time()) - lookback_hours * 3600
    like_clauses = " OR ".join(["n.content LIKE ?"] * len(EVENT_KEYWORDS))
    like_params = [f"%{kw}%" for kw in EVENT_KEYWORDS]
    sql = f"""
        SELECT n.cluster_id, MAX(n.importance) AS importance,
               MAX(n.ts) AS ts,
               (SELECT content FROM news n2 WHERE n2.cluster_id = n.cluster_id
                ORDER BY LENGTH(content) DESC LIMIT 1) AS content
        FROM news n
        LEFT JOIN verifications v ON v.cluster_id = n.cluster_id
        WHERE n.ts >= ?
          AND v.cluster_id IS NULL
          AND ({like_clauses})
        GROUP BY n.cluster_id
        HAVING importance >= 2
        ORDER BY ts DESC
        LIMIT ?
    """
    rows = conn.execute(sql, (since, *like_params, limit)).fetchall()
    cols = ["cluster_id", "importance", "ts", "content"]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        content = d["content"] or ""
        if looks_like_summary(content):
            continue
        if find_event_keyword(content):
            out.append(d)
    return out


def verify_batch(max_n: int = 5, sleep_between: float = 2.0) -> list[dict]:
    """Verify up to max_n eligible clusters. Returns list of verification rows."""
    conn = connect()
    candidates = find_eligible(conn, limit=max_n * 3)[:max_n]
    results = []
    for c in candidates:
        r = verify_cluster(conn, c["cluster_id"], c["content"], c["ts"])
        results.append({**c, **r})
        time.sleep(sleep_between)
    return results


if __name__ == "__main__":
    import sys
    if "--scan" in sys.argv:
        conn = connect()
        rows = find_eligible(conn, limit=50)
        print(f"=== {len(rows)} eligible clusters (importance>=2, has event keyword) ===")
        for r in rows:
            entity, kind = extract_entity(r["content"])
            kw = find_event_keyword(r["content"])
            print(f"  cluster {r['cluster_id']}  imp={r['importance']}  "
                  f"entity={entity!r}({kind})  kw={kw!r}")
            print(f"    {r['content'][:90]}")
    else:
        results = verify_batch(max_n=int(sys.argv[1]) if len(sys.argv) > 1 else 5)
        print(f"verified {len(results)} clusters:")
        for r in results:
            print(f"  cluster {r['cluster_id']}  -> {r['status']}  "
                  f"company={r.get('company')!r}  kw={r.get('keyword')!r}")
            if r.get("evidence"):
                print(f"    evidence: {r['evidence'][:120]}")
