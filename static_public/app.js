// News Radar — BYOK frontend
// Architecture:
//   * News flow: fetch /public/news.json (static, refreshed by exporter worker)
//   * Summaries: call visitor's own LLM API key directly from browser
//   * No backend API call for summaries — keys never leave browser

// ============================================================================
// 0. State + helpers
// ============================================================================
const $ = (id) => document.getElementById(id);
// Relative URL — resolves correctly both:
//   * local dev (FastAPI at /public/): /public/news.json
//   * GitHub Pages deploy (same dir): /newsradar-public/news.json
const NEWS_JSON_URL = "news.json";
const POLL_INTERVAL_MS = 30_000;
const TOPICS = [
  "矛盾点", "多头信号", "空头信号", "地缘政治",
  "宏观变化", "政策监管", "AI/人工智能", "半导体产业链",
];
const WINDOWS = { "1h": 3600, "5h": 18_000, "24h": 86_400 };

const DEFAULT_MODELS = {
  anthropic: "claude-sonnet-4-5",
  openai:    "gpt-4o-mini",
};

const state = {
  minImportance: 2,
  activeWindow: "1h",
  clusters: [],
  lastJsonTs: 0,
  summarizing: false,
};

// All times display in 北京时间 (Asia/Shanghai), regardless of visitor's
// browser timezone. Use Intl.DateTimeFormat with explicit timeZone.
const _BJ_HM = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai",
});
const _BJ_FULL = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai",
});
const fmtTime = (ts) => _BJ_HM.format(new Date(ts * 1000));
const fmtFullTime = (ts) => _BJ_FULL.format(new Date(ts * 1000));
const fmtAgo = (ts) => {
  let s = Math.floor(Date.now()/1000) - ts;
  if (s < 0) s = 0;   // clock skew / wrong-tz tolerance
  if (s < 60) return `${s}s 前`;
  if (s < 3600) return `${Math.floor(s/60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s/3600)} 小时前`;
  return `${Math.floor(s/86400)} 天前`;
};
const escapeHtml = (s) => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

// ============================================================================
// 1. LLM config: stored in localStorage only
// ============================================================================
const CFG_KEY = "newsradar.llm.config";

function loadConfig() {
  try { return JSON.parse(localStorage.getItem(CFG_KEY) || "null"); }
  catch { return null; }
}
function saveConfig(cfg) { localStorage.setItem(CFG_KEY, JSON.stringify(cfg)); }
function clearConfig() {
  localStorage.removeItem(CFG_KEY);
  $("cfg-key").value = "";
  $("cfg-baseurl").value = "";
  updateConfigBtn();
  renderSummary(state.activeWindow);
}
function updateConfigBtn() {
  const cfg = loadConfig();
  const b = $("config-btn");
  if (!cfg || !cfg.api_key) {
    b.classList.add("warn");
    b.textContent = "⚙ 需要 LLM 设置";
  } else {
    b.classList.remove("warn");
    b.textContent = `⚙ ${cfg.provider}/${cfg.model}`;
  }
}

// ============================================================================
// 2. News JSON loader (polling)
// ============================================================================
async function loadNews() {
  try {
    const r = await fetch(NEWS_JSON_URL + `?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    state.clusters = data.clusters || [];
    state.lastJsonTs = data.generated_at;
    updateDataState(data);
    renderFeed();
  } catch (e) {
    setDot("fail");
    $("data-state").textContent = `加载失败：${e.message}`;
  }
}

function updateDataState(data) {
  const ago = fmtAgo(data.latest_ts || data.generated_at);
  if (data.is_stale) {
    setDot("stale");
    $("data-state").textContent = `数据已过期（最新条目 ${ago}）`;
  } else {
    setDot("ok");
    $("data-state").textContent = `已同步 · 最新 ${ago}`;
  }
}
function setDot(cls) {
  const d = $("status-dot");
  d.classList.remove("ok", "stale", "fail");
  if (cls) d.classList.add(cls);
}

// ============================================================================
// 3. Feed rendering
// ============================================================================
function renderFeed() {
  const items = state.clusters
    .filter(c => c.importance >= state.minImportance);
  $("feed-count").textContent = items.length;
  if (!items.length) {
    $("feed-list").innerHTML = `<div class="placeholder">无符合条件的快讯</div>`;
    return;
  }
  $("feed-list").innerHTML = items.map(renderFeedItem).join("");
}
function renderFeedItem(it) {
  const cls = `imp-${it.importance}`;
  const xBadge = it.source_count > 1 ? `<span class="feed-x-badge">×${it.source_count}</span>` : "";
  const verifyBadge = it.verified ? `<span class="verified-badge">✓ 已核实</span>` : "";
  return `
    <div class="feed-item ${cls}" data-cluster="${it.cluster_id}"
         onclick="showCluster(${it.cluster_id})">
      <div class="feed-meta">
        <div class="feed-meta-left">
          <span class="feed-time">${fmtTime(it.ts)}</span>
          <span class="feed-sources">${(it.sources||[]).join(",")}</span>
          ${xBadge}${verifyBadge}
        </div>
        <span class="feed-mute">#${it.cluster_id}</span>
      </div>
      <div class="feed-content">${escapeHtml(it.content || "")}</div>
    </div>`;
}

function showCluster(cid) {
  const c = state.clusters.find(x => x.cluster_id === cid);
  if (!c) return;
  document.querySelectorAll(".feed-item").forEach(el => el.classList.remove("highlight"));
  const tgt = document.querySelector(`.feed-item[data-cluster="${cid}"]`);
  if (tgt) { tgt.classList.add("highlight"); tgt.scrollIntoView({behavior:"smooth", block:"center"}); }

  $("cluster-modal").classList.remove("hidden");
  $("modal-content").innerHTML = `
    <h3>Cluster #${cid} · ${c.source_count} 源</h3>
    <div class="modal-row">
      <div class="modal-row-meta">
        ${(c.sources||[]).join(", ")} · ${fmtFullTime(c.ts)} · imp=${c.importance}
        ${c.verified ? ' · <span style="color:var(--green)">✓ iFinD 已核实</span>' : ""}
      </div>
      <div class="modal-row-content">${escapeHtml(c.content || "")}</div>
    </div>
    <div class="config-help" style="margin-top:12px">
      公开版只显示去重后的代表条目。完整多源原文需联系站长查看。
    </div>`;
}
function closeModal() { $("cluster-modal").classList.add("hidden"); }

// ============================================================================
// 4. Summary cache (localStorage)
// ============================================================================
async function clusterHash(cids) {
  const s = [...cids].sort((a,b)=>a-b).join(",");
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return Array.from(new Uint8Array(buf)).slice(0,8).map(b=>b.toString(16).padStart(2,"0")).join("");
}
function summaryCacheKey(window, hash, modelKey) {
  return `nr.summary.${window}.${modelKey}.${hash}`;
}
function loadSummaryCache(window, hash, modelKey) {
  try {
    const raw = localStorage.getItem(summaryCacheKey(window, hash, modelKey));
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}
function saveSummaryCache(window, hash, modelKey, data) {
  try {
    localStorage.setItem(summaryCacheKey(window, hash, modelKey),
      JSON.stringify({ generated_at: Date.now()/1000|0, ...data }));
  } catch (e) { console.warn("cache write failed", e); }
}

// ============================================================================
// 5. Prompt + LLM call (provider-specific)
// ============================================================================
function buildPrompt(window, clustersInWindow) {
  const lines = clustersInWindow.map(c => {
    const t = fmtTime(c.ts);
    const m = c.importance >= 3 ? "★" : (c.importance >= 2 ? "·" : " ");
    const v = c.verified ? " [✓核实]" : "";
    const body = (c.content || "").replace(/\n/g, " ").slice(0, 200);
    return `[${String(c.cluster_id).padStart(4)}|${m}|${t}|${(c.sources||[]).join(",")}]${v} ${body}`;
  }).join("\n");

  return `你是金融市场雷达的合成层。给定时间窗 ${window} 内的去重快讯（每行一条已去重的事件），按 8 个主题输出**结构化 JSON**摘要。

# 严格要求
1. **只输出 JSON**，不要任何前后缀、解释、寒暄、markdown 代码块标记。
2. 必须严格符合下方 schema。
3. 每个主题输出 0-5 个要点；**没有相关内容时返回空数组 []**，不要硬凑。
4. 每个要点是一个对象：{"point": "<≤80 字的判断>", "evidence": "<引用关键快讯片段>", "cluster_ids": [<相关 cluster_id 数组>]}。
5. 关注**信息增量**，不要复述全部快讯；偏好"对市场可能造成的反应/方向"。
6. 主题"矛盾点"专门记录快讯之间相互冲突或方向不一致的信号。
7. 主题"多头信号 / 空头信号"基于 A 股 / 港股 / 美股视角，结合宏观与公司层面的具体催化。
8. 主题"地缘政治 / 宏观变化 / 政策监管"按字面分类。
9. 主题"AI/人工智能 / 半导体产业链"专门记录这两条产业链的事件、订单、政策、技术进展。

# 输出 schema（8 个 key 全部必须存在）
{
  "矛盾点":        [{"point":"...","evidence":"...","cluster_ids":[..]}],
  "多头信号":      [...],
  "空头信号":      [...],
  "地缘政治":      [...],
  "宏观变化":      [...],
  "政策监管":      [...],
  "AI/人工智能":   [...],
  "半导体产业链":  [...]
}

# 时间窗：最近 ${window}
# 快讯条数：${clustersInWindow.length}

# 快讯
${lines}`;
}

async function callLLM(prompt, cfg) {
  if (cfg.provider === "anthropic") return callAnthropic(prompt, cfg);
  if (cfg.provider === "openai")    return callOpenAI(prompt, cfg);
  throw new Error(`unsupported provider: ${cfg.provider}`);
}

async function callAnthropic(prompt, cfg) {
  const url = (cfg.base_url || "https://api.anthropic.com") + "/v1/messages";
  const r = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": cfg.api_key,
      "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true",
    },
    body: JSON.stringify({
      model: cfg.model || DEFAULT_MODELS.anthropic,
      max_tokens: 4096,
      messages: [{ role: "user", content: prompt }],
    }),
  });
  if (!r.ok) {
    const err = await r.text();
    throw new Error(`Anthropic ${r.status}: ${err.slice(0,300)}`);
  }
  const data = await r.json();
  const text = (data.content || []).filter(c => c.type === "text").map(c => c.text).join("");
  return text;
}

async function callOpenAI(prompt, cfg) {
  const base = (cfg.base_url || "https://api.openai.com").replace(/\/$/, "");
  const url = `${base}/v1/chat/completions`;
  const r = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "authorization": `Bearer ${cfg.api_key}`,
    },
    body: JSON.stringify({
      model: cfg.model || DEFAULT_MODELS.openai,
      messages: [{ role: "user", content: prompt }],
      response_format: { type: "json_object" },
    }),
  });
  if (!r.ok) {
    const err = await r.text();
    throw new Error(`OpenAI ${r.status}: ${err.slice(0,300)}`);
  }
  const data = await r.json();
  return data.choices?.[0]?.message?.content || "";
}

function extractJson(text) {
  text = text.trim();
  if (text.startsWith("```")) {
    text = text.split("\n").filter(l => !l.trim().startsWith("```")).join("\n").trim();
  }
  try { return JSON.parse(text); } catch {}
  const first = text.indexOf("{"), last = text.lastIndexOf("}");
  if (first >= 0 && last > first) return JSON.parse(text.slice(first, last + 1));
  throw new Error("Could not parse JSON from LLM output: " + text.slice(0,200));
}

// ============================================================================
// 6. Summary generation flow
// ============================================================================
async function generateSummary() {
  if (state.summarizing) return;
  const cfg = loadConfig();
  if (!cfg || !cfg.api_key) { openConfig(); return; }

  const win = state.activeWindow;
  const cutoff = Math.floor(Date.now()/1000) - WINDOWS[win];
  const inWindow = state.clusters.filter(c => c.ts >= cutoff)
                                 .sort((a,b) => b.importance - a.importance || b.ts - a.ts)
                                 .slice(0, win === "1h" ? 60 : (win === "5h" ? 80 : 100));
  if (!inWindow.length) {
    $("topics").innerHTML = `<div class="placeholder">本时间窗内没有快讯数据。</div>`;
    return;
  }

  const hash = await clusterHash(inWindow.map(c => c.cluster_id));
  const modelKey = `${cfg.provider}-${cfg.model || "default"}`;
  const cached = loadSummaryCache(win, hash, modelKey);
  if (cached) {
    renderSummaryFromData(win, cached);
    $("summary-meta").textContent += "（缓存）";
    return;
  }

  state.summarizing = true;
  $("summarize-btn").disabled = true;
  $("summarize-btn").textContent = "生成中…";
  $("topics").innerHTML = `<div class="loading">正在调用 ${cfg.provider}/${cfg.model || "(default)"} 摘要 ${inWindow.length} 条快讯…<br>通常耗时 30-90 秒</div>`;

  try {
    const t0 = Date.now();
    const prompt = buildPrompt(win, inWindow);
    const rawText = await callLLM(prompt, cfg);
    const topics = extractJson(rawText);
    // normalize
    for (const t of TOPICS) if (!Array.isArray(topics[t])) topics[t] = [];
    const data = {
      window: win, cluster_count: inWindow.length,
      topics, model: cfg.model || "default",
      elapsed_ms: Date.now() - t0,
    };
    saveSummaryCache(win, hash, modelKey, data);
    renderSummaryFromData(win, data);
  } catch (e) {
    $("topics").innerHTML = `<div class="placeholder need-key">
      生成摘要失败：${escapeHtml(e.message)}<br><br>
      <small>检查 key/model/base_url 是否正确；或试试切换 provider。</small>
    </div>`;
  } finally {
    state.summarizing = false;
    $("summarize-btn").disabled = false;
    $("summarize-btn").textContent = "✨ 生成摘要";
  }
}

function renderSummary(win) {
  state.activeWindow = win;
  document.querySelectorAll(".win-tab").forEach(b => b.classList.toggle("active", b.dataset.win === win));

  // try cache-only render (no LLM call); if miss, show hint
  const cfg = loadConfig();
  if (!cfg || !cfg.api_key) {
    $("summary-meta").textContent = "";
    $("topics").innerHTML = `<div class="placeholder need-key">
      请先点击右上角「LLM 设置」配置你的 API key，然后点击「✨ 生成摘要」。
    </div>`;
    return;
  }

  // try to find newest cache for this window
  const modelKey = `${cfg.provider}-${cfg.model || "default"}`;
  // We don't know cluster_hash without recomputing; just show "click summarize" hint
  $("summary-meta").textContent = "";
  $("topics").innerHTML = `<div class="placeholder">
    点击「✨ 生成摘要」用你的 LLM key 生成 ${win} 摘要。
    <br><small>已生成过的同一时间窗 + 同一 cluster 集合会从浏览器缓存直接读取。</small>
  </div>`;
}

function renderSummaryFromData(win, data) {
  $("summary-meta").textContent = `${data.cluster_count} 条 cluster · ${data.model} · ${(data.elapsed_ms/1000).toFixed(1)}s`;
  const topics = data.topics || {};
  $("topics").innerHTML = TOPICS.map(t => renderTopicCard(t, topics[t] || [])).join("");
}

function renderTopicCard(topic, points) {
  const slug = topic.replace(/\/.*/, "").replace(/人工智能/, "AI");
  const body = points.length === 0
    ? `<div class="topic-empty">本时间窗内无相关信号</div>`
    : points.map(renderPoint).join("");
  return `
    <div class="topic-card">
      <div class="topic-title t-${slug}"><span class="icon"></span>${topic}</div>
      ${body}
    </div>`;
}
function renderPoint(p) {
  const chips = (p.cluster_ids || []).map(cid =>
    `<span class="chip" onclick="event.stopPropagation();showCluster(${cid})">c${cid}</span>`
  ).join("");
  const chipsBox = chips ? `<span class="cluster-chips">${chips}</span>` : "";
  const evidence = p.evidence ? `<div class="evidence">${escapeHtml(p.evidence)}</div>` : "";
  return `<div class="topic-point">
    <div class="point-text">${escapeHtml(p.point || "")} ${chipsBox}</div>
    ${evidence}
  </div>`;
}

// ============================================================================
// 7. Config modal
// ============================================================================
function openConfig() {
  const cfg = loadConfig() || {};
  $("cfg-provider").value = cfg.provider || "anthropic";
  $("cfg-key").value = cfg.api_key || "";
  $("cfg-model").value = cfg.model || DEFAULT_MODELS[cfg.provider || "anthropic"];
  $("cfg-baseurl").value = cfg.base_url || "";
  updateModelHint();
  $("config-modal").classList.remove("hidden");
}
function closeConfig() { $("config-modal").classList.add("hidden"); }

function updateModelHint() {
  const p = $("cfg-provider").value;
  const hints = {
    anthropic: "推荐 claude-sonnet-4-5 或 claude-3-5-sonnet-20241022",
    openai:    "OpenAI: gpt-4o-mini / DeepSeek: deepseek-chat / Moonshot: moonshot-v1-32k",
  };
  $("model-hint").textContent = hints[p] || "";
  if (!$("cfg-model").value) $("cfg-model").value = DEFAULT_MODELS[p] || "";
}

// ============================================================================
// 8. Wire-up
// ============================================================================
document.querySelectorAll(".win-tab").forEach(b => {
  b.addEventListener("click", () => renderSummary(b.dataset.win));
});
$("min-importance").addEventListener("change", e => {
  state.minImportance = parseInt(e.target.value, 10); renderFeed();
});
$("refresh-btn").addEventListener("click", loadNews);
$("summarize-btn").addEventListener("click", generateSummary);
$("config-btn").addEventListener("click", openConfig);
$("cfg-provider").addEventListener("change", () => {
  $("cfg-model").value = DEFAULT_MODELS[$("cfg-provider").value] || "";
  updateModelHint();
});
$("config-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const cfg = {
    provider: $("cfg-provider").value,
    api_key: $("cfg-key").value.trim(),
    model: $("cfg-model").value.trim(),
    base_url: $("cfg-baseurl").value.trim(),
  };
  if (!cfg.api_key) { alert("请填入 API key"); return; }
  saveConfig(cfg);
  closeConfig();
  updateConfigBtn();
  renderSummary(state.activeWindow);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeModal(); closeConfig(); }
});

window.showCluster = showCluster;
window.closeModal = closeModal;
window.closeConfig = closeConfig;
window.clearConfig = clearConfig;

// ============================================================================
// Boot
// ============================================================================
updateConfigBtn();
loadNews();
renderSummary("1h");
setInterval(loadNews, POLL_INTERVAL_MS);
