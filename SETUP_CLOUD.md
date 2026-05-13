# 部署到 GitHub Pages + Actions（单仓库版，零成本）

> 让任何人开 URL 就能看到 News Radar，访客自带 LLM key。
> Repo 是公开的，Actions 用内置 GITHUB_TOKEN 提交，不用 PAT、不用第二个仓库。

---

## 总览

```
┌──────────── 公开 newsradar 仓库 ─────────────┐
│  源码 + cloud_export.py + Actions             │
│  Actions: 每 15 分钟跑 cloud_export.py        │
│           → commit static_public/news.json    │
│                                               │
│  GitHub Pages: https://<user>.github.io/      │
│                newsradar/                     │
│      ← 来源：main 分支的 static_public/       │
└──────────────────────────────────────────────┘
```

**成本**：0
**更新频率**：news.json 每 15 分钟刷一次（cron 会有 5-15 分钟随机延迟，所以实际间隔可能 15-30 分钟）
**LLM**：访客在浏览器填自己的 API key
**Actions 公开仓库**：无限分钟数

---

## Step 1 — Repo 改成 Public

1. 打开 `https://github.com/Zhangchen-bit/newsradar/settings`
2. 滚到最下面 "Danger Zone" → "Change visibility"
3. 改成 Public，按提示确认仓库名

> 此前已审计过 repo，**无任何 API key / token / 敏感数据**。

---

## Step 2 — 启用 GitHub Pages

1. 打开 `https://github.com/Zhangchen-bit/newsradar/settings/pages`
2. **Source**：Deploy from a branch
3. **Branch**：`main`
4. **Folder**：`/static_public`
5. Save

等 1-2 分钟，页面顶部会出现：
> Your site is live at `https://Zhangchen-bit.github.io/newsradar/`

打开这个 URL，前端能加载（但 `news.json` 还没生成，所以快讯流空着）。

---

## Step 3 — 手动触发一次 Actions 把 news.json 灌进去

1. 打开 `https://github.com/Zhangchen-bit/newsradar/actions`
2. 左侧选 `Refresh news.json`
3. 右上角 **Run workflow** → 选 main 分支 → Run

等 30-60 秒看到绿色 ✓。Actions 会自动 commit 一条 `data: refresh ...`。

第一次跑完后再刷新 Pages URL，左栏应该出现快讯流。

---

## Step 4 — 等 cron 自动跑

Workflow 已经设 `*/15 * * * *`，之后什么都不用管。

注意 GitHub cron **不是精确触发**，常延迟 5-15 分钟才跑。要更频繁可以改成 `*/10`，公开仓库无 Actions 分钟限制。

---

## 改频率 / 临时停掉 / 重新触发

- **改频率**：编辑 `.github/workflows/refresh-news.yml` 的 `cron`
- **临时停**：Actions 页面 → Refresh news.json → 右上角 ··· → Disable workflow
- **手动重跑**：Actions 页面 → Refresh news.json → Run workflow

---

## 常见坑

| 问题 | 排查 |
|---|---|
| 网页一直显示"数据已过期" | 看 Actions 日志，可能全部源都失败了 |
| Actions 日志说 `cls.cn timeout` | GitHub runner 在美国/欧洲，财联社经常拒境外 IP。代码已做容错——只要 jin10+wscn 通就照常出 JSON，`source_status` 字段会标 cls 失败 |
| Pages 没出现 / 一直 404 | 设置里检查 Branch=main、Folder=`/static_public`；首次部署可能要 5-10 分钟 |
| `news.json` 一直在 commit 也没更新内容 | 看 cloud_export.py 是否能在本地跑通（`python3 cloud_export.py --out /tmp/x.json`） |
| commit 太频繁污染 git log | 当前每 15 分钟一次。**未来嫌脏**可以改成 push 到独立 `data` 分支并 force-push（保留主分支干净），需要时让我改 |

---

## 进阶：减少 commit 噪音（可选）

如果你不想 main 分支被 Actions 频繁 commit 撑大，可以把 news.json 放到一个 `data` 独立分支，每次 force-push 覆盖单一 commit，主分支永远干净：

```yaml
# 替换 workflow 末尾的 Commit if changed
- name: Push to data branch (orphan, force)
  run: |
    git config user.email "actions@github.com"
    git config user.name "newsradar-bot"
    git checkout --orphan data
    git rm -rf . > /dev/null
    cp static_public/news.json .
    git add news.json
    git commit -m "$(date -u +%Y-%m-%dT%H:%MZ)"
    git push -f origin data
```

然后改前端 `app.js`：
```js
const NEWS_JSON_URL = "https://raw.githubusercontent.com/Zhangchen-bit/newsradar/data/news.json";
```

这种方式 main 分支永远只有源码变更，data 分支永远只有 1 个 commit。代价：raw.githubusercontent.com 有 5 分钟 CDN 缓存（影响最坏 +5 分钟延迟）。

**当前不做**，先用简单方案跑起来。
