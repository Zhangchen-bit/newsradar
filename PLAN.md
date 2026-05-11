# News Radar 落地计划

> 目标：复刻 amlone.net/tools/newsradar 的核心能力——多源快讯聚合 + LLM 多窗口主题摘要——并嵌入到既有的"投资计划"工作流中。
>
> 本文档是**总纲**，覆盖数据层 → 处理层 → LLM 层 → 前端。当前只执行 **P1（数据采集）**，后续阶段按需推进。

---

## 0. 范围与不做

**做**：
- 多源快讯实时采集（金十、华尔街见闻、财联社为主，东财/新浪为备）
- 统一字段、去重、重要性归一化
- LLM 按 1h/5h/24h 三个时间窗 × 8 个主题做摘要
- 简洁前端（自用，不做权限/账号系统）

**不做**：
- 多用户、登录、付费墙
- 自建新闻源（不爬未公开渠道）
- 历史回溯超过 7 天（只做实时 + 短期窗口）
- 复杂的情绪量化指标（先用 LLM 文本判断）

---

## 1. 总体架构

```
┌──────────────── 采集层 (P1) ────────────────┐
│  jin10 poller    每 5s                       │
│  wscn poller     每 5s     ─┐                │
│  cls poller      每 3s      │                │
│  em  poller      每 10s     │                │
│  ifind 校验补丁  事件触发    │                │
└────────────────────────────│─────────────────┘
                             ↓
┌──────────────── 归一化层 (P2) ───────────────┐
│  字段统一 / SimHash 去重 / 重要性映射          │
└────────────────────────────│─────────────────┘
                             ↓
                  SQLite (news.db)  ← P1 终点
                             │
                             ↓
┌──────────────── 推送层 (P4) ─────────────────┐
│  SSE 推前端，新条目即推                       │
└────────────────────────────│─────────────────┘
                             ↓
┌──────────────── LLM 摘要层 (P5) ─────────────┐
│  时间窗切片(1h/5h/24h) × 主题(8) → prompt     │
│  并发调用用户配置的 LLM                       │
└─────────────────────────────────────────────┘
                             ↓
                       前端 (P6)
```

---

## 2. 数据源详表

### 主力源

| 源 | 接口 | 频率 | 字段(原始) | 重要性映射 |
|---|---|---|---|---|
| 金十 | `https://www.jin10.com/flash_newest.js` (JSONP) <br> `https://flash-api.jin10.com/get_flash_list` (JSON, 需 x-app-id / x-version 头) | 5s | id, time, data.content, important(0/1), channel, tags | important=1 → 3，否则 1 |
| 华尔街见闻 | `https://api-one.wallstreetcn.com/apiv1/content/lives?channel=global-channel&limit=30` | 5s | id, display_time, content_text, title, score, channels | score≥3 → 3, ≥1 → 2, else 1 |
| 财联社 | `https://www.cls.cn/nodeapi/updateTelegraphList` | 3s | id, ctime, content, title, level, type | level=A → 3, else 1（红色电报 → 3） |

### 备份源（P1 不接，留 P2 加）

| 源 | 接口 | 用途 |
|---|---|---|
| 东方财富 7x24 | `np-listapi.eastmoney.com/comm/wap/getListInfo` | 散户视角冗余 |
| 新浪 7x24 | `zhibo.sina.com.cn/api/zhibo/feed` | 主源全挂时兜底 |
| iFinD | `THS_NewsList` / `THS_RealtimeQuotes` | 公告核验、行情联动 |

### 反爬注意
- 全部加常规 `User-Agent` + `Referer`
- 金十 flash-api 必须带 `x-app-id: bVBF4FyRTn5NJF5n`, `x-version: 1.0.0`（社区通用值）
- 见闻偶发 CF 校验 → 失败重试 + 切镜像 `api-one-wscn.awtmt.com`
- 财联社需要 `sign` 参数（MD5）；如不通则改用其 web 端 `cls.cn/telegraph` HTML 解析兜底

---

## 3. 统一数据模型

```python
@dataclass
class NewsItem:
    source_id: str        # 源原始 id
    source: str           # "jin10" / "wscn" / "cls"
    ts: int               # unix 秒
    title: str            # 没有就用 content 前 30 字
    content: str          # 正文
    importance: int       # 0-3 (3 = 红色重要)
    tags: list[str]       # 原始 tag 透传
    url: str              # 原文链接 (有则填)
    raw: dict             # 原始 JSON，留作调试
```

**SQLite 表 `news`**:
```sql
CREATE TABLE news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    importance INTEGER DEFAULT 1,
    tags TEXT,             -- JSON array
    url TEXT,
    simhash TEXT,          -- 64-bit hex, 留给 P2
    cluster_id INTEGER,    -- 同事件多源归并, 留给 P2
    raw TEXT,              -- JSON
    created_at INTEGER NOT NULL,
    UNIQUE(source, source_id)
);
CREATE INDEX idx_news_ts ON news(ts DESC);
CREATE INDEX idx_news_source_ts ON news(source, ts DESC);
```

---

## 4. 分阶段计划

### P1 — 采集底座（**当前执行**，预计 1 天）
- [x] 目录脚手架
- [ ] `db.py`：SQLite 初始化 + 插入接口（UPSERT on `(source, source_id)`）
- [ ] `pollers/jin10.py`：拉 flash-api，解析，写库
- [ ] `pollers/wscn.py`：拉见闻 lives，解析，写库
- [ ] `pollers/cls.py`：拉财联社电报，解析，写库
- [ ] `run_all.py`：以子进程方式拉起三个 poller，统一日志
- [ ] 冒烟：跑 60 秒，确认三源都有数据落库

**P1 验收标准**：
- `python run_all.py` 启动后 1 分钟内，`news` 表能看到三个 source 各 ≥1 条
- 重启不重复入库（UNIQUE 约束生效）
- 单源失败不影响其他源（每个 poller 独立循环 + try/except）

### P2 — 归一化与去重（0.5 天）
- 重要性映射统一到 0-3
- SimHash + 5 分钟时间窗合并同事件（写入 `cluster_id`）
- iFinD 公告核验：对包含"中标/收购/重大合同"关键词的快讯，自动查公司公告做事实标记

### P3 — 简易查询接口（0.5 天）
- FastAPI 暴露 `/api/news?since=...&min_importance=...&source=...`
- `/api/stream` SSE 推送新条目

### P4 — LLM 摘要层（1 天）
- 时间窗切片器：取最近 1h/5h/24h 快讯
- 8 个主题的 prompt 模板（矛盾点、多空信号、地缘、宏观、政策、行业中观、AI、半导体）
- 并发调 LLM（用户配置 key），结果存 `summaries` 表
- 触发：每 5 分钟跑一次 1h 窗，每 15 分钟跑 5h 窗，每小时跑 24h 窗

### P5 — 前端（1 天）
- 单页：左栏快讯流（SSE），右栏 3 个时间窗 × 8 主题卡片
- 极简：Vanilla JS + 一个 CSS 框架（Pico.css 或 Tailwind CDN）

### P6 — 运维与监控（0.5 天）
- 单源连续失败 5 分钟 → 钉钉/邮件告警
- 每天 0 点归档昨日 `news` 到 `news_archive`，主表只留 7 天

---

## 5. 目录结构

```
newsradar/
├── PLAN.md                  ← 本文件
├── README.md                ← 启动说明（P1 末尾写）
├── db.py                    ← SQLite 封装
├── run_all.py               ← 总启动器
├── pollers/
│   ├── __init__.py
│   ├── base.py              ← 公共基类（重试/UA/日志）
│   ├── jin10.py
│   ├── wscn.py
│   └── cls.py
├── data/
│   └── news.db              ← SQLite 文件（运行后生成）
└── logs/
    ├── jin10.log
    ├── wscn.log
    └── cls.log
```

---

## 6. 风险与未决项

| 风险 | 应对 |
|---|---|
| 财联社接口要 `sign` 参数 | 先尝试无 sign 调用，失败则切 HTML 解析；或抓包确认 sign 算法 |
| 金十/见闻被风控限频 | 5s 轮询保守，UA 轮换；触发 429 退避到 30s |
| LLM 摘要在低快讯量时段产出"无信息" | prompt 加约束："数据不足时直接返回空"，前端识别空结果折叠卡片 |
| iFinD token 共享给本服务的鉴权机制 | P2 再处理，先复用本机已有 token 文件 |

---

## 7. 当前执行说明

本轮只跑 **P1**。完成后会在 `newsradar/README.md` 补一份启动说明，并在控制台展示三源采集到的最新 5 条作为验收证据。
