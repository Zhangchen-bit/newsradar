# 部署到 GitHub Pages + Actions（零成本）

> 让任何人开 URL 就能看到 News Radar，访客自带 LLM key。
> 你的私有代码不公开，只有去重后的新闻 JSON 走公网。

---

## 总览

```
┌──────────── 私有 newsradar 仓库 ─────────────┐
│  源码 + .github/workflows/refresh-news.yml    │
│  Actions: 每 15 分钟跑 cloud_export.py        │
│           → REST API PUT 到下面这个公开仓     │
└──────────────────────│─────────────────────┘
                       ↓
┌──────────── 公开 newsradar-public 仓库 ──────┐
│  index.html / style.css / app.js  (前端)     │  ← 一次性手动 push
│  news.json                                    │  ← Actions 自动覆盖
│                                               │
│  GitHub Pages: https://Zhangchen-bit.github   │
│                .io/newsradar-public/          │
└──────────────────────────────────────────────┘
```

**成本**：0
**更新频率**：news.json 每 15 分钟刷一次
**LLM**：访客在浏览器填自己 API key，钱各付各的
**Actions 月用量**：~96 次/天 × 30 = 2880 次，每次 ~25-40 秒 = **1200-1900 分钟/月**（私有仓库免费额度 2000 分钟）

---

## Step 1 — 在 GitHub 上建公开仓库

1. 打开 https://github.com/new
2. 名字：`newsradar-public`
3. 选 **Public**
4. 勾选 "Add a README"（随便，会被覆盖）
5. 点 Create

---

## Step 2 — 一次性把前端推上去

```bash
cd /Users/apple/Desktop/投资计划/newsradar
git clone git@github.com:Zhangchen-bit/newsradar-public.git /tmp/nrp
cp static_public/index.html /tmp/nrp/
cp static_public/style.css  /tmp/nrp/
cp static_public/app.js     /tmp/nrp/
cd /tmp/nrp
git add -A
git commit -m "frontend"
git push
```

未来前端代码改了，**重复这一步**就行（覆盖 3 个文件）。

---

## Step 3 — 在公开仓库开启 GitHub Pages

1. 打开 `https://github.com/Zhangchen-bit/newsradar-public/settings/pages`
2. **Source**：Deploy from a branch
3. **Branch**：`main` + `/` (root)
4. Save
5. 等 1-2 分钟，提示页面上会出现 `Your site is live at https://Zhangchen-bit.github.io/newsradar-public/`

打开这个 URL，应该能看到雷达界面（但 `news.json` 还没生成，所以快讯流是空的）。

---

## Step 4 — 生成 PAT 让 Actions 能写公开仓

1. 打开 https://github.com/settings/personal-access-tokens/new
2. 选 **Fine-grained tokens**
3. Token name：`newsradar-data-write`
4. Expiration：选你舒服的（建议 90 天，到期再续）
5. Repository access：**Only select repositories** → 选 `newsradar-public`
6. Permissions → Repository permissions →
   - **Contents**: Read and write
7. 点 Generate token → 复制下来（只显示一次）

---

## Step 5 — 把 PAT + 目标仓库路径设为私有仓库的 secret

1. 打开 `https://github.com/Zhangchen-bit/newsradar/settings/secrets/actions`
2. 点 **New repository secret**：
   - Name：`DATA_REPO_PAT`
   - Value：粘贴刚才的 PAT
3. 再 **New repository secret**：
   - Name：`DATA_REPO_PATH`
   - Value：`Zhangchen-bit/newsradar-public`

---

## Step 6 — 手动触发一次 Actions 验证

1. 打开 `https://github.com/Zhangchen-bit/newsradar/actions`
2. 左侧选 `Refresh news.json`
3. 右上角 **Run workflow** → Run
4. 等 30-60 秒，看到绿色 ✓

打开 `https://github.com/Zhangchen-bit/newsradar-public` 应该能看到 `news.json` 出现，再开雷达 URL 就有数据了。

---

## Step 7 — 等 cron 自动跑

Workflow 已经设了 `*/15 * * * *`（每 15 分钟一次）。之后什么都不用管。

GitHub Actions cron **可能延迟 5-15 分钟**触发（这是 GitHub 的已知行为），所以实际刷新间隔可能是 15-30 分钟。要更准时可以缩短到 `*/10`，但要算好 Actions 额度。

---

## 改频率 / 临时停掉

- **改频率**：编辑 `.github/workflows/refresh-news.yml` 的 `cron` 字段
- **临时停**：去 Actions 页面右上角点 `...` → Disable workflow
- **手动触发**：Actions 页面 → Refresh news.json → Run workflow

---

## 常见坑

| 问题 | 排查 |
|---|---|
| Actions 失败，提示 `cls.cn timeout` | GitHub runner 在美国/欧洲，财联社可能限制境外 IP。`cloud_export.py` 已做"部分失败容忍"，只要 jin10 + wscn 成功就照常出 JSON。`source_status` 字段会标注哪家失败 |
| `news.json` 没更新 | 看 Actions 日志：`https://github.com/Zhangchen-bit/newsradar/actions` |
| PAT 过期 | 重生成一个，更新 secret |
| 网页一直显示"数据已过期" | 检查 news.json 的 `is_stale` 字段；可能 Actions 没跑 / 全部数据源都失败了 |
| GitHub Pages 没出现 | 设置里检查 Branch / Folder；GitHub Pages 首次部署可能要 5-10 分钟 |

---

## 下一步（如果觉得 15 分钟太慢）

- 换成自己的 VPS + cron：每分钟跑 `cloud_export.py`，rsync 到 Cloudflare Pages 或自建 nginx
- 用 Cloudflare Workers + KV：Actions 推到 KV，访客读取延迟 < 100ms
- 上 Vercel / Cloudflare Pages：连私有仓库，前端独立部署，news.json 还是走 GitHub Pages 公开仓
