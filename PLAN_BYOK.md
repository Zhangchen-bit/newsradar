# News Radar — BYOK 版本重构计划

> **目的**：把当前"必须在 cccc 本机跑 + 烧 cccc 配额"的雷达，改造成"**任何人开 URL 就能用，LLM 钱各付各的**"的模式（参考 amlone.net/tools/newsradar 的设计）。
>
> **状态**：本文是设计文档，**未执行**。等 cccc 拍板后再启动。

---

## 1. 为什么要做这个版本

当前架构（V1）面临的问题：

| 痛点 | 现状 |
|---|---|
| 给别人看就要把本机暴露 | 必须 Tailscale / ngrok / 局域网 |
| LLM 摘要烧 cccc 的 Claude Pro 额度 | 多人共用 → 每小时 9 次远不够 |
| 没有公开 URL | 没法做产品 / 没法发朋友圈 |
| iFinD 公告核验是私有能力 | 别人没法用（也不该用 cccc 的 token） |

BYOK（Bring Your Own Key）模式把上面的痛点同时解掉：
- **新闻流**是公共可见的（站长出小钱采集 + 静态托管）
- **LLM 摘要**让访客填自己的 API key，浏览器直连模型 API → 谁用谁付
- **iFinD 核验**作为"高级功能"对登录的 cccc 自己可见，对访客隐藏

---

## 2. 架构变化总览

```
当前 V1：                                BYOK V2：
┌───────────────────────┐               ┌──────────────────────────────┐
│ cccc 本机             │               │ 公网静态站（Vercel等）        │
│  pollers              │               │  index.html + app.js          │
│  dedup                │               │       │                       │
│  iFinD 核验           │               │       ↓ fetch /news.json      │
│  LLM 摘要 (claude -p) │               │  CDN 上的预生成 JSON          │
│  FastAPI :8765        │               │  /news.json (1min 一次更新)   │
│  SQLite               │               │  /summary_24h.json (可选)     │
└───────────────────────┘               └──────────────────────────────┘
        ↑                                        ↑
   只有 cccc 用                              全世界都能开
                                                ↓
                                       浏览器调访客自己的 LLM API
                                       Anthropic / OpenAI / Gemini

cccc 的本机仍然运行：
  pollers + dedup + iFinD 核验  → 输出 /news.json
                                → 推到 CDN（rsync / git push / S3）
  LLM 摘要 worker：可选保留（仅 cccc 自己看高级版）
```

**关键拆分**：
- **共享层（站长付钱）**：新闻采集、去重、cluster 合并 → 输出 JSON 静态文件
- **个人层（访客付钱）**：LLM 摘要 → 浏览器直连 LLM API
- **私有层（仅 cccc）**：iFinD 公告核验、行情联动 → 不暴露给访客

---

## 3. 数据流详解

### 3.1 共享数据流（人人可见）

```
本机 cccc:
  pollers (jin10/wscn/cls) → SQLite (内部状态)
                          ↓ 每 60 秒导出
                          /export/news_public.json   (最近 24h 去重 cluster)
                          /export/news_recent.json   (最近 5 min 增量，给 SSE)
                          ↓ 推送
  GitHub Actions / 手动 rsync / S3 sync
                          ↓
  公网静态托管（Vercel / Cloudflare Pages / S3+CloudFront）
                          ↓ GET / SSE
  访客浏览器
```

**JSON 格式约定**（front-end 直接消费）：

```json
// /export/news_public.json
{
  "generated_at": 1778500000,
  "clusters": [
    {
      "cluster_id": 76,
      "ts": 1778510278,
      "importance": 2,
      "source_count": 3,
      "sources": ["jin10", "wscn", "cls"],
      "title": "创想三维 IPO 聆讯",
      "content": "...",
      "tags": ["港股IPO动态"],
      "verified": false                  // 简化字段，访客不需要看核验细节
    },
    ...
  ]
}
```

### 3.2 BYOK 摘要流（访客本地）

```
访客浏览器：
  1. 启动时检查 localStorage 里有没有 LLM 配置
  2. 没有 → 弹窗让访客填：
     - provider: anthropic / openai / gemini
     - api_key
     - model
     - base_url（可选，方便代理）
  3. 有配置 → 拉 /news.json
  4. 用户切到 5h 窗 → JS 截取窗口内的 cluster → 构造 prompt → 直连 LLM
  5. 解析 JSON 响应 → 渲染 8 主题卡片
  6. 把摘要缓存到 localStorage（key = window + cluster_hash），下次同窗口同数据直接读缓存
```

**关键决策**：
- **LLM 调用在浏览器，不经过任何后端**（Anthropic/OpenAI/Gemini 都支持 browser-side CORS）
- API key 只存 localStorage，永不上传
- prompt 模板从 `/static/prompt_8topics.txt` 加载（站长可统一更新）

### 3.3 私有层（仅 cccc，URL 上看不见）

iFinD 公告核验、`claude -p` 摘要、本地 SQLite 全部保留在 cccc 机器，**不导出**到公网 JSON。
访问 `https://your-domain/admin?token=xxx` 才能看，普通访客看不到核验徽章。

---

## 4. 改造工作量分解

| 阶段 | 工作 | 时长 |
|---|---|---|
| **B1** | 写 `exporter.py`：从 SQLite 每分钟生成 `news_public.json` | 2h |
| **B2** | 改前端：加 LLM 配置弹窗、localStorage 持久化 | 3h |
| **B3** | 改前端：把"调 `/api/summary`"换成"调 LLM API"（按 provider 分别处理） | 4h |
| **B4** | 加 prompt 模板文件 + 错误处理 + 流式输出（可选） | 2h |
| **B5** | 部署：选 GitHub Pages / Vercel / Cloudflare Pages，加自动推送 | 2h |
| **B6**(可选) | "管理员视图"：cccc 凭 token 看 iFinD 核验 / 写记忆 | 3h |

**总计**：约 **1.5-2 天**，不含调试。

---

## 5. 部署方案对比

| 方案 | 静态托管 | JSON 更新机制 | 月成本 |
|---|---|---|---|
| **GitHub Pages + GitHub Actions** | 免费 | Actions 每 N 分钟 commit 新 JSON（脏但可用） | 0 |
| **Vercel** | 免费 | 你本机 push 到 GitHub 触发部署 | 0 |
| **Cloudflare Pages + R2** | 免费 | 本机 rsync 到 R2 | 0 |
| **自家 VPS + nginx** | $5/月 | 本机 rsync | $5 |

**推荐 Cloudflare Pages**：免费、全球 CDN、支持自定义域名、可绑 R2 用 Workers 做轻量代理。

---

## 6. 风险与决策点

### 6.1 浏览器直连 Anthropic / OpenAI 的 CORS

- **Anthropic**：默认**不支持** browser-side 调用（必须服务端），但有 `anthropic-dangerous-direct-browser-access: true` header 可强制开启
- **OpenAI**：原生支持 browser-side
- **Gemini**：原生支持 browser-side
- **DeepSeek / Moonshot / 通义**：OpenAI 兼容协议，通常支持

→ 写代码时按 provider 分支处理 header

### 6.2 prompt 在客户端 → 可被任意修改

访客理论上能改 prompt 让 LLM 输出乱七八糟的东西，但这只会糟蹋他自己的体验，不影响其他人。可接受。

### 6.3 iFinD 核验数据要不要暴露

**强烈不要暴露**。原因：
- iFinD 是 cccc 的私有订阅，公开会违反 ToS
- 核验结果"已核实"是 cccc 的差异化能力，公开就失去价值
- 给访客看核验徽章但点击进去看不到细节，体验也不好

→ B6 阶段只在 admin URL 里可见。

### 6.4 数据延迟

JSON 静态文件 vs 实时 SSE：
- 静态 JSON 每分钟更新一次 → 延迟 1-60 秒（最坏情况）
- 静态 + 前端轮询：访客每 30 秒拉一次 → 总延迟 30-90 秒
- 真实时 SSE：需要服务端常驻，跟 BYOK 理念矛盾（除非用 Cloudflare Workers）

→ B 版本接受 **1 分钟延迟**。amlone 看起来也是这种延迟级别。

### 6.5 cccc 自己怎么用

V1 不删，继续在本机跑：
- 访客访问 → V2 公网 URL
- cccc 自己用 → V1 内网 URL 127.0.0.1:8765（保留 iFinD 核验 + LLM 摘要）
- V1 多导出一份 JSON 给 V2 用

两个版本**共存不冲突**。

---

## 7. 与 amlone 的差异（保留你的特色）

amlone 的核心是"展示 + 让用户用自己钱总结"。如果只是复刻就和它一样了。建议保留这些**差异化**：

| 你的优势 | 怎么体现 |
|---|---|
| **三源去重 + cluster 追溯** | UI 上仍然显示 ×2/×3 徽章 + 点 chip 看源 |
| **完整中文金融场景** | prompt 模板特化（amlone 的主题更泛） |
| **可选 iFinD 核验**（admin 模式） | 给访客留一个"高级会员"挂钩——以后想商业化可以走这条路 |

---

## 8. 决策点（等 cccc 拍板）

1. **要不要做 B 版本**？还是先把 V1 稳定运行一阵看效果再说？
2. **如果做，部署到哪？** GitHub Pages / Vercel / Cloudflare Pages
3. **域名要自有的吗？** 还是先用 `xxx.vercel.app` 这种系统域名
4. **iFinD 核验要不要给"早期内测用户"看？** 一个简单 token 鉴权可加

---

## 9. 不做的事

- 不做用户系统、登录、邀请码（BYOK 自带"鉴权"——没 key 看不到摘要）
- 不做付费功能（短期没必要）
- 不做手机 App（响应式 web 足够）
- 不做历史回放（每天只看最近 24h，过期数据丢弃）
