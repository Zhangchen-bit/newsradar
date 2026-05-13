# News Radar — 启动说明

## 环境
- Python: `/usr/local/bin/python3.12`
- 依赖：`requests`（已装）；`curl` 系统自带

## 文件结构
```
newsradar/
├── PLAN.md               总体计划（P1~P6）
├── README.md             本文件
├── db.py                 SQLite 封装
├── run_all.py            一键启动三个 poller
├── pollers/
│   ├── base.py           HTTP/日志/循环工具
│   ├── jin10.py          金十快讯
│   ├── wscn.py           华尔街见闻
│   └── cls.py            财联社电报
├── data/news.db          数据落库（运行后生成）
└── logs/                 各 poller 日志
```

## 启动

**一键启动三源**：
```bash
cd /Users/apple/Desktop/投资计划/newsradar
/usr/local/bin/python3.12 run_all.py
```

任一 poller 崩溃会被 `run_all.py` 自动重启（5s 后）。

**单源调试（拉一次就退出）**：
```bash
/usr/local/bin/python3.12 pollers/jin10.py --once
/usr/local/bin/python3.12 pollers/wscn.py  --once
/usr/local/bin/python3.12 pollers/cls.py   --once
```

**查看库里的内容**：
```bash
sqlite3 data/news.db "SELECT source, COUNT(*) FROM news GROUP BY source;"
sqlite3 data/news.db "SELECT ts, source, substr(content,1,60) FROM news ORDER BY ts DESC LIMIT 20;"
```

## P1 验收记录（2026-05-11）

首轮冒烟：
| 源 | 入库条数 | 状态 |
|---|---|---|
| jin10 | 24 | ✓ |
| wscn | 31 | ✓（主域 TLS 被 CDN 拦截，已切镜像 `api-one-wscn.awtmt.com`） |
| cls | 27 | ✓（必须 `--noproxy '*'`，本机代理 7897 转 cls.cn 会卡） |

60 秒连续轮询：所有 tick 均成功，新条目 0（UNIQUE 去重生效）。

## 已踩的坑（留给后续）

1. **华尔街见闻主域 TLS 指纹被拒**：`api-one.wallstreetcn.com` 对 Python openssl 握手返回 EOF；切镜像 `api-one-wscn.awtmt.com` 即正常。已实现自动 fallback。
2. **财联社不走本机代理**：本机 `HTTPS_PROXY=http://127.0.0.1:7897` 转发 cls.cn 会超时；poller 内部调 `curl --noproxy '*'` 绕过。
3. **财联社 `ad` 字段是 dict 不是 bool**：当字典空但存在时不能直接 truth 判断，要看 `ad.id`。
4. **金十 `important=1` → importance=3**；见闻 `score>=3` → 3、`>=1` → 2；财联社 `level=A` → 3。映射规则在各 poller 内。

## P2 验收记录（2026-05-11）

新增组件：
- `dedup.py` — 64-bit SimHash（CJK 字符 bigram + 数字 token），Hamming 阈值 14，时间窗 ±10 分钟
- `db.py` — `insert_many` 在写入前自动算 simhash 并分配 `cluster_id`
- `backfill_clusters.py` — 给老数据回填 cluster
- `query.py` — 去重视图 `feed()`：每个 cluster 一行，取最长正文作代表，标注 `source_count` 和 `sources`

校准依据（200 条真实数据）：
- 随机对 hamming 中位数 31，p10=26
- 跨源同事件 10-15
- 相关但不同事件（如卡拉斯系列发言）15-18
- → 阈值 14 是 precision/recall 平衡点

端到端验证：
- 93 行全部 classified，0 个未分簇
- 创想三维 IPO 三源合并（jin10+cls+wscn）✓
- 国联绿色科技递表两源合并（cls+wscn）✓

**查看去重后的雷达流**：
```bash
/usr/local/bin/python3.12 query.py     # 全部
/usr/local/bin/python3.12 query.py 2   # 仅 importance≥2
```

## P2.5 验收记录（2026-05-11）

iFinD MCP 公告核验：

新增组件：
- `verifier.py` — 实体抽取（公司名/股票代码）+ 事件关键词触发 + iFinD MCP `news/search_notice` 调用 + 分类
- `run_verifier.py` — 后台 worker，每 60s 扫一次未核验 cluster，每次最多 5 条
- DB 新表 `verifications`：`cluster_id, status, company, keyword, query, evidence, evidence_date, raw, checked_at`
- `query.py::feed()` JOIN 核验状态，输出带徽章 `[✓核实]/[△媒体]/[?未证]`
- `run_all.py` 已挂入 verifier worker，与三个 poller 一起被托管

设计要点：
- 触发：cluster importance ≥ 2 **且** 内容命中事件关键词（中标/收购/合同/股权/获批/订单/增减持/回购/定增/分红/重组…）
- 过滤：跳过"晚间精选/新闻汇总"类多事件汇总贴（正则识别"精选|盘点|汇总"或 ≥3 个编号列表项）
- 实体抽取：6 位股票代码 > 【】内公司名 > 行内 `[CJK]+(股份|集团|科技|有限公司|...)` 后缀
- 时间窗：以快讯 ts 为中心 ±3 天
- 分类：notice 命中 → confirmed；news 命中但 notice 没有 → partial；都没有 → unconfirmed

iFinD MCP 客户端：`~/.claude/skills/ifind-mcp-research-cn/scripts/ifind_mcp_client.js`，调用 `news/search_notice` 子工具。

端到端验证：
- "国芯科技：国家集成电路产业投资基金股份有限公司拟减持不超过1.37%。" → confirmed ✓
- iFinD 返回了对应公告"《国芯科技：关于持股5%以下的股东减持股份计划公告》"
- 90s 端到端测试：4 poller + verifier 全部稳定，无错误

踩坑：
- **SQL LIMIT 在关键词过滤之前截断**：原本在 Python 端 post-filter 关键词，但 SQL LIMIT 已先把候选切掉。改为把关键词 LIKE 条件下推到 SQL WHERE 子句
- **summary 类汇总贴会污染实体抽取**：例如"晚间新闻精选"里有多个事件，正则只能抓到第一个公司名。已加 `looks_like_summary()` 过滤

**查看带核验徽章的雷达流**：
```bash
/usr/local/bin/python3.12 query.py 2
```

**单独跑核验（一次性）**：
```bash
/usr/local/bin/python3.12 verifier.py --scan   # 看哪些 cluster 候选
/usr/local/bin/python3.12 verifier.py 5        # 核验最多 5 条
```

## P4 验收记录（2026-05-11）

LLM 多窗口主题摘要：

新增组件：
- `llm.py` — `claude -p` headless 子进程封装，强 JSON 解析（容错 code fence、首尾杂质）
- `summarizer.py` — 时间窗切片 + 8 主题 prompt + cluster_hash 缓存
- `run_summarizer.py` — 调度器：1h/15min、5h/30min、24h/60min
- DB 新表 `summaries(window, generated_at, cluster_hash, cluster_count, model, topics_json, elapsed_ms)`
- `query.py` 新增 `print_summary(window)` 视图
- `run_all.py` 已挂入 summarizer worker

设计要点：
- **打包成单次调用**：8 主题在 1 个 prompt 内返回 JSON，相比每主题独立调用省 8 倍额度
- **8 主题**：矛盾点、多头信号、空头信号、地缘政治、宏观变化、政策监管、AI/人工智能、半导体产业链
- **每个要点强制带 `cluster_ids`**：可追溯到源快讯（query.py 显示为 `[c#xx,yy]`）
- **缓存**：cluster_id 集合的 md5 作 cache key，cluster 没变就跳过 LLM 调用
- **额度预算**（Pro ≈ 9 次/小时）：调度后 7 次/小时，留 2 次/小时给用户日常用

LLM：Claude Sonnet 4.6 (`claude-sonnet-4-6`)，单次调用 60-100s，输入 50-80 cluster

端到端验证：
- 5h 窗 80 cluster → 97s 完成，输出 25+ 条要点，矛盾点抓到"芯片股 vs 指数背离"和"伊朗主权 vs 特朗普谈判"等交叉信号
- 1h 窗 55 cluster → 102s 完成
- 重复调用同窗口 → 缓存命中 0ms

踩坑：
- LLM 偶尔在 JSON 前后加 markdown code fence 或空行 → 解析器先 strip code fence，再用"首 `{` 到末 `}`"提取
- 解析失败时把原始输出落盘到 `logs/llm_debug_parse_fail_*.txt`，便于事后排查
- `claude -p` 默认走全局 CLAUDE.md 的"称呼 cccc"规则 → prompt 必须明确"只输出 JSON，不要任何前缀"

**查看摘要**：
```bash
/usr/local/bin/python3.12 query.py 1h    # 1 小时窗
/usr/local/bin/python3.12 query.py 5h    # 5 小时窗
/usr/local/bin/python3.12 query.py 24h   # 24 小时窗
```

**手动跑摘要**：
```bash
/usr/local/bin/python3.12 summarizer.py 5h
/usr/local/bin/python3.12 summarizer.py 5h --force   # 绕过缓存
```

## P3 + P5 验收记录（2026-05-11）

后端 + 前端单页一并落地。

新增组件：
- `api.py` — FastAPI 应用：
  - `GET /api/news?since=&min_importance=&limit=` 去重快讯流
  - `GET /api/cluster/{cluster_id}` cluster 的全部源 + 核验信息
  - `GET /api/summary?window=1h|5h|24h` 单窗口摘要
  - `GET /api/summaries` 一次拉三个窗口
  - `GET /api/stream` SSE：新 cluster / 新摘要推送
  - `GET /` 静态前端入口
- `run_api.py` — uvicorn 启动器（监听 127.0.0.1:8765）
- `static/index.html` + `style.css` + `app.js` — 暗色风格单页：
  - 左栏：快讯流（按重要度过滤、可勾选"仅已核实"）
  - 右栏：1h/5h/24h Tab 切换 × 8 主题卡片
  - 摘要要点的 `[c#xx]` chip 可点击打开 cluster 详情弹窗
  - SSE 自动同步：新条目高亮 + 摘要更新提示
- `run_all.py` 已挂入 `run_api`，与 poller/verifier/summarizer 一起托管

端到端验证：
- `/api/news` 返回 cluster 列表 ✓
- `/api/summary?window=5h` 返回完整 8 主题 JSON ✓
- `/api/cluster/76` 返回创想三维三源数据 ✓
- `/api/stream` SSE 握手 + keepalive 正常 ✓
- 静态文件（CSS/JS）200 ✓

**启动后访问**：`http://127.0.0.1:8765`

## 启动整套（推荐）

```bash
cd /Users/apple/Desktop/投资计划/newsradar
/usr/local/bin/python3.12 run_all.py
```

这会同时拉起：
- 3 个新闻采集 poller（jin10/wscn/cls）
- iFinD 公告核验 worker
- LLM 摘要 worker
- FastAPI 服务

**只启动后端 + 前端**（已有数据时）：
```bash
/usr/local/bin/python3.12 run_api.py
# 浏览器打开 http://127.0.0.1:8765
```

## V1 全部阶段完成

P1（采集）/ P2（去重）/ P2.5（iFinD 核验）/ P3（API）/ P4（LLM 摘要）/ P5（前端）均已落地。

## B 版（BYOK）落地（2026-05-13）

为了能把雷达**分享给任何人**而又不烧站长 LLM 配额，新增 BYOK 公开版（参见 [PLAN_BYOK.md](PLAN_BYOK.md)）：

| 组件 | 文件 | 功能 |
|---|---|---|
| 静态导出器 | `exporter.py` | 把去重后的 cluster feed 导出为 `static_public/news.json` |
| 导出 worker | `run_exporter.py` | 每 60s 重导，由 `run_all.py` 托管 |
| BYOK 前端 | `static_public/index.html` + `style.css` + `app.js` | 公开 URL，访客自带 LLM key |
| FastAPI 挂载 | `api.py` 加 `/public` 路由 | 本地开发同时跑 V1 admin + V2 BYOK |

**BYOK 数据流**：
```
访客浏览器:
  GET /public/news.json   ← 静态文件，站长出小钱托管
  POST 访客填的 LLM API    ← 访客自己付费，钱不经过站长
  摘要结果缓存到 localStorage（cluster_hash 作 key）
```

**支持的 provider**：Anthropic（Claude，需开启浏览器直连 header）、OpenAI 兼容（OpenAI / DeepSeek / Moonshot / 通义……）。

**访问入口**：
- V1 admin（含 iFinD 核验徽章 + 后端 LLM 摘要）：`http://127.0.0.1:8765/`
- V2 BYOK 公开版（访客自带 key）：`http://127.0.0.1:8765/public/`

**部署 BYOK**（V2 → 真正能被陌生人用）：
1. 把 `static_public/` 整个目录推到 Cloudflare Pages / Vercel / GitHub Pages
2. 在本机用 cron 把 `static_public/news.json` `rsync` 或推到 GitHub 上
3. 域名指过去即可

## 下一步（可选）
- **B5 部署**：当前 BYOK 仅在本机可访问，做 Cloudflare Pages 部署 + GitHub Actions 同步 JSON
- **P6**：监控告警 + 历史归档清理
