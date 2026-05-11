// News Radar frontend logic
const $ = (id) => document.getElementById(id);
const fmtTime = (ts) => {
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const fmtFullTime = (ts) => {
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

const VERIFY_LABEL = {
  confirmed: "✓ 公告核实",
  partial:   "△ 媒体报道",
  unconfirmed: "? 未证实",
};

const state = {
  minImportance: 2,
  onlyVerified: false,
  feedItems: [],
  activeWindow: "1h",
  summaries: {},
};

// ---------- Feed ----------
async function loadFeed() {
  try {
    const r = await fetch(`/api/news?min_importance=${state.minImportance}&limit=80`);
    const data = await r.json();
    state.feedItems = data.items || [];
    renderFeed();
  } catch (e) {
    $("feed-list").innerHTML = `<div class="placeholder">加载失败: ${e}</div>`;
  }
}

function renderFeed() {
  const list = $("feed-list");
  let items = state.feedItems;
  if (state.onlyVerified) {
    items = items.filter(it => it.verify_status === "confirmed");
  }
  $("feed-count").textContent = items.length;
  if (!items.length) {
    list.innerHTML = `<div class="placeholder">无符合条件的快讯</div>`;
    return;
  }
  list.innerHTML = items.map(it => renderFeedItem(it)).join("");
}

function renderFeedItem(it) {
  const impClass = `imp-${it.importance}`;
  const xBadge = it.source_count > 1 ? `<span class="feed-x-badge">×${it.source_count}</span>` : "";
  const verify = it.verify_status && VERIFY_LABEL[it.verify_status]
    ? `<span class="verify-badge ${it.verify_status}">${VERIFY_LABEL[it.verify_status]}</span>`
    : "";
  return `
    <div class="feed-item ${impClass}" data-cluster="${it.cluster_id}"
         onclick="showCluster(${it.cluster_id})">
      <div class="feed-meta">
        <div class="feed-meta-left">
          <span class="feed-time">${fmtTime(it.latest_ts)}</span>
          <span class="feed-sources">${it.sources}</span>
          ${xBadge}
          ${verify}
        </div>
        <span class="feed-mute">#${it.cluster_id}</span>
      </div>
      <div class="feed-content">${escapeHtml(it.content || "")}</div>
    </div>
  `;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------- Summaries ----------
async function loadSummaries() {
  try {
    const r = await fetch("/api/summaries");
    state.summaries = await r.json();
    renderSummary(state.activeWindow);
  } catch (e) {
    $("topics").innerHTML = `<div class="placeholder">加载失败: ${e}</div>`;
  }
}

function renderSummary(win) {
  state.activeWindow = win;
  document.querySelectorAll(".win-tab").forEach(b => {
    b.classList.toggle("active", b.dataset.win === win);
  });
  const s = state.summaries[win];
  const meta = $("summary-meta");
  if (!s || !s.generated_at) {
    meta.textContent = "暂无摘要";
    $("topics").innerHTML = `<div class="placeholder">尚未生成 ${win} 摘要——summarizer worker 启动后会自动产出</div>`;
    return;
  }
  meta.textContent = `生成于 ${fmtFullTime(s.generated_at)} · ${s.cluster_count} 条 cluster · ${s.model}`;

  const topics = s.topics || {};
  const order = ["矛盾点", "多头信号", "空头信号", "地缘政治",
                 "宏观变化", "政策监管", "AI/人工智能", "半导体产业链"];
  $("topics").innerHTML = order.map(t => renderTopicCard(t, topics[t] || [])).join("");
}

function renderTopicCard(topic, points) {
  const cls = "topic-" + topic.replace(/[\/]/g, "").replace(/人工智能/, "AI");
  const body = points.length === 0
    ? `<div class="topic-empty">本时间窗内无相关信号</div>`
    : points.map(renderPoint).join("");
  return `
    <div class="topic-card">
      <div class="topic-title ${cls}"><span class="icon"></span>${topic}</div>
      ${body}
    </div>
  `;
}

function renderPoint(p) {
  const chips = (p.cluster_ids || []).map(cid =>
    `<span class="chip" onclick="event.stopPropagation();showCluster(${cid})">c${cid}</span>`
  ).join("");
  const chipsBox = chips ? `<span class="cluster-chips">${chips}</span>` : "";
  const evidence = p.evidence
    ? `<div class="evidence">${escapeHtml(p.evidence)}</div>`
    : "";
  return `
    <div class="topic-point">
      <div class="point-text">${escapeHtml(p.point || "")} ${chipsBox}</div>
      ${evidence}
    </div>
  `;
}

// ---------- Cluster modal ----------
async function showCluster(cid) {
  // also highlight in feed
  document.querySelectorAll(".feed-item").forEach(el => el.classList.remove("highlight"));
  const target = document.querySelector(`.feed-item[data-cluster="${cid}"]`);
  if (target) {
    target.classList.add("highlight");
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  $("cluster-modal").classList.remove("hidden");
  $("modal-content").innerHTML = `<div class="placeholder">加载中…</div>`;
  try {
    const r = await fetch(`/api/cluster/${cid}`);
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    const items = data.items || [];
    const v = data.verification;
    const verifyHtml = v ? `
      <div class="verify-box">
        <span class="vlabel">iFinD 核验</span>
        <div>状态：<b>${VERIFY_LABEL[v.status] || v.status}</b>　公司：${v.company || ""}　日期：${v.evidence_date || ""}</div>
        ${v.evidence ? `<div style="margin-top:6px;color:var(--fg-dim);font-size:11px;white-space:pre-wrap">${escapeHtml(v.evidence.substring(0, 500))}</div>` : ""}
      </div>` : "";

    $("modal-content").innerHTML = `
      <h3>Cluster #${cid} · ${items.length} 源</h3>
      ${verifyHtml}
      ${items.map(it => `
        <div class="modal-row">
          <div class="modal-row-meta">
            <span style="color:var(--accent)">${it.source}</span> ·
            ${fmtFullTime(it.ts)} ·
            imp=${it.importance} ·
            #${it.source_id}
            ${it.url ? ` · <a href="${escapeHtml(it.url)}" target="_blank" style="color:var(--accent)">原文 ↗</a>` : ""}
          </div>
          <div class="modal-row-content">${escapeHtml(it.content || "")}</div>
        </div>
      `).join("")}
    `;
  } catch (e) {
    $("modal-content").innerHTML = `<div class="placeholder">加载失败: ${e.message || e}</div>`;
  }
}

function closeModal() {
  $("cluster-modal").classList.add("hidden");
}

// ---------- SSE ----------
function setupSSE() {
  const ev = new EventSource("/api/stream");
  ev.addEventListener("hello", () => $("conn-state").textContent = "已连接 · 实时同步");
  ev.addEventListener("news", (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.items && d.items.length) loadFeed();  // reload to keep filter consistent
    } catch {}
  });
  ev.addEventListener("summary", (e) => {
    try {
      const d = JSON.parse(e.data);
      loadSummaries();
      $("conn-state").textContent = `${d.window} 摘要已更新`;
      setTimeout(() => $("conn-state").textContent = "已连接 · 实时同步", 3000);
    } catch {}
  });
  ev.onerror = () => {
    $("conn-state").textContent = "断线 · 5s 后重连";
    ev.close();
    setTimeout(setupSSE, 5000);
  };
}

// ---------- Wire up ----------
document.querySelectorAll(".win-tab").forEach(b => {
  b.addEventListener("click", () => renderSummary(b.dataset.win));
});
$("min-importance").addEventListener("change", (e) => {
  state.minImportance = parseInt(e.target.value, 10);
  loadFeed();
});
$("only-verified").addEventListener("change", (e) => {
  state.onlyVerified = e.target.checked;
  renderFeed();
});
$("refresh-btn").addEventListener("click", () => { loadFeed(); loadSummaries(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

window.showCluster = showCluster;
window.closeModal = closeModal;

// Initial load
loadFeed();
loadSummaries();
setupSSE();
