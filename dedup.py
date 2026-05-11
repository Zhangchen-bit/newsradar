"""SimHash-based dedup + cross-source clustering.

Strategy:
  1. For each new news item, compute 64-bit SimHash from CJK character bigrams.
  2. Within a ±CLUSTER_WINDOW_SEC time window, scan existing clusters; if any
     cluster member has Hamming distance <= CLUSTER_HAMMING_THRESHOLD, attach
     to that cluster. Otherwise mint a new cluster_id.
  3. Cluster ids monotonically increase; survival = same event across sources.

The window is intentionally wider than 5 min to catch slow secondary coverage
(jin10 reporting an event 8-10 min after the official disclosure on cls).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3

BITS = 64
CLUSTER_WINDOW_SEC = 10 * 60   # ±10 min window
CLUSTER_HAMMING_THRESHOLD = 14
# Calibrated 2026-05-11 on 200 real cross-source flashes:
#   random-pair hamming: p10=26, median=31
#   true cross-source duplicates: 10-15
#   related-but-different (e.g. two Kallas statements): 15-18
# Threshold 14 captures true dups without merging templated variants.

_CJK_RE = re.compile(r"[^\w一-鿿]+")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")


def _tokenize(text: str) -> list[str]:
    """CJK bigrams + numeric tokens. Numbers preserved as full tokens because
    in financial flashes the digits are the load-bearing semantics."""
    if not text:
        return []
    nums = _NUM_RE.findall(text)
    cleaned = _CJK_RE.sub("", text)
    bigrams = [cleaned[i : i + 2] for i in range(len(cleaned) - 1)] or [cleaned]
    return bigrams + nums


def simhash(text: str) -> int:
    feats = _tokenize(text)
    if not feats:
        return 0
    v = [0] * BITS
    for feat in feats:
        h = int(hashlib.md5(feat.encode("utf-8")).hexdigest()[:16], 16)
        for i in range(BITS):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(BITS):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def to_hex(h: int) -> str:
    return f"{h:016x}"


def from_hex(s: str | None) -> int:
    return int(s, 16) if s else 0


def assign_cluster(
    conn: sqlite3.Connection,
    ts: int,
    content: str,
    *,
    importance: int = 1,
) -> tuple[str, int]:
    """Return (simhash_hex, cluster_id) for a candidate row.

    Looks up existing rows within the time window; if any has hamming <=
    threshold, reuse its cluster_id. Otherwise allocate a fresh one.
    """
    h = simhash(content)
    h_hex = to_hex(h)

    lo, hi = ts - CLUSTER_WINDOW_SEC, ts + CLUSTER_WINDOW_SEC
    rows = conn.execute(
        "SELECT simhash, cluster_id FROM news "
        "WHERE ts BETWEEN ? AND ? AND simhash IS NOT NULL AND cluster_id IS NOT NULL",
        (lo, hi),
    ).fetchall()

    best_cluster = None
    best_dist = CLUSTER_HAMMING_THRESHOLD + 1
    for sh_hex, cid in rows:
        d = hamming(h, from_hex(sh_hex))
        if d < best_dist:
            best_dist = d
            best_cluster = cid
            if d == 0:
                break

    if best_cluster is not None and best_dist <= CLUSTER_HAMMING_THRESHOLD:
        return h_hex, best_cluster

    # mint a new cluster id
    row = conn.execute("SELECT COALESCE(MAX(cluster_id), 0) + 1 FROM news").fetchone()
    return h_hex, int(row[0])


if __name__ == "__main__":
    # quick smoke
    a = "深圳市创想三维科技股份有限公司通过港交所上市聆讯"
    b = "【深圳市创想三维科技股份有限公司通过港交所上市聆讯】财联社5月11日电"
    c = "国际油价短线拉升，WTI原油期货价格涨2.18%"
    ha, hb, hc = simhash(a), simhash(b), simhash(c)
    print(f"a~b hamming = {hamming(ha, hb)}  (expect small)")
    print(f"a~c hamming = {hamming(ha, hc)}  (expect large)")
