# 部署到 VPS（30 元/月，1 分钟刷新）

> 用一台云服务器替代 GitHub Actions cron，彻底解决"不刷新"问题。
> 仍然保持 BYOK：访客自带 LLM key，VPS 只负责采集 + 静态托管。

---

## 0. 选哪家云

### 推荐：阿里云轻量服务器（国内 → 国内体验最好）

- 地址：https://www.aliyun.com/product/swas
- 规格：**2核 2G 3M 带宽**（最便宜的 ~¥30/月，足够）
- 地区：**杭州 / 上海 / 北京** 任选（这些能直连金十 / 见闻 / 财联社）
- 操作系统：**Ubuntu 22.04**
- 不绑域名只用 IP → **无需备案**

### 备选

| 方案 | 月成本 | 备案 | 适合 |
|---|---|---|---|
| 腾讯云轻量 | ¥30 起 | 同上 | 备选 |
| Vultr / DigitalOcean | $5 (¥35) | 不需要 | 给海外人看 |
| Hetzner CAX11 (ARM) | €3.79 (¥30) | 不需要 | 海外，便宜 |

> ⚠️ 海外 VPS 访问 cls.cn 可能慢或被拒，jin10 / wscn 一般可以。

---

## 1. 买完后准备工作（控制台）

阿里云轻量后台：

1. **重置实例密码**（设一个你记得住的 root 密码）
2. **防火墙规则** → 添加端口：
   - 协议 TCP，端口 `8765`，源 IP `0.0.0.0/0`
   - 这是 News Radar 的访问端口

记下：**实例公网 IP**（控制台首页能看到）

---

## 2. SSH 登入

```bash
ssh root@<你的公网IP>
```

输入刚才设的密码即可。

---

## 3. 一键部署脚本

登入后**直接整段粘贴执行**：

```bash
set -e

# 1. 装依赖
apt update
apt install -y python3 python3-venv python3-pip git curl

# 2. 拉代码
mkdir -p /opt
cd /opt
[ -d newsradar ] || git clone https://github.com/Zhangchen-bit/newsradar.git
cd newsradar

# 3. 创建虚拟环境
python3 -m venv venv
venv/bin/pip install --quiet requests

# 4. 写 systemd 服务 A：每 60s 跑 cloud_export.py，生成 news.json
cat > /etc/systemd/system/newsradar-export.service <<'EOF'
[Unit]
Description=News Radar exporter loop (60s)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/newsradar
ExecStart=/bin/bash -c 'while true; do /opt/newsradar/venv/bin/python cloud_export.py --out /opt/newsradar/static_public/news.json --window 24 || true; sleep 60; done'
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 5. 写 systemd 服务 B：HTTP 静态服务（端口 8765）
cat > /etc/systemd/system/newsradar-web.service <<'EOF'
[Unit]
Description=News Radar static HTTP server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/newsradar/static_public
ExecStart=/usr/bin/python3 -m http.server 8765 --bind 0.0.0.0
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 6. 启动并设开机自启
systemctl daemon-reload
systemctl enable --now newsradar-export newsradar-web

# 7. 等 60 秒看 news.json 生成
sleep 65
ls -la /opt/newsradar/static_public/news.json
echo
echo "==== 搞定！访问: http://<你的公网IP>:8765/ ===="
```

整段执行完毕，访问：

> **`http://<你的公网IP>:8765/`**

预期看到完整的 News Radar 界面，左栏快讯流，右栏可填 LLM key 生成摘要。

---

## 4. 日常维护

### 查状态

```bash
systemctl status newsradar-export   # 采集 + 导出循环
systemctl status newsradar-web      # HTTP 服务
```

### 看日志

```bash
journalctl -u newsradar-export -f   # 实时日志
journalctl -u newsradar-export --since "10 min ago" | tail -50
```

### 拉最新代码

```bash
cd /opt/newsradar
git pull
systemctl restart newsradar-export newsradar-web
```

### 改刷新频率

编辑 `/etc/systemd/system/newsradar-export.service`，把 `sleep 60` 改成你想要的秒数（**最低建议 30s**，更快意义不大且可能被源站限频）。

```bash
systemctl daemon-reload
systemctl restart newsradar-export
```

---

## 5. 升级路径

### 加域名（可选）

如果你想要 `radar.yourdomain.com` 而不是 IP:8765：

1. 买一个 `.com` 或 `.cn` 域名（¥40-60/年）
2. DNS A 记录指到 VPS 公网 IP
3. 装 Caddy 自动 HTTPS：

```bash
apt install -y caddy
cat > /etc/caddy/Caddyfile <<EOF
radar.yourdomain.com {
    reverse_proxy localhost:8765
}
EOF
systemctl reload caddy
```

Caddy 会自动签 Let's Encrypt 证书，访问变成 `https://radar.yourdomain.com`。

> ⚠️ 国内云 + 国内域名走 80/443 默认要 ICP 备案（7-15 天）。
> 解决：用海外 VPS + 海外域名（如 .com）+ Cloudflare DNS → 不用备案。

### 加 iFinD 公告核验（可选，给你自己看）

如果你想在 VPS 上也跑 iFinD 核验（V1 admin 功能）：
1. 把 `~/.codex/secrets/ifind_mcp_config.json` scp 到 VPS
2. 装 Node.js 和 iFinD MCP client
3. 改 `cloud_export.py` 让它调 verifier（输出到一个独立的 admin URL，不公开）

复杂度高，建议先跑通基础版再考虑。

---

## 6. GitHub 这条路彻底关掉？

不需要。建议**双轨并行**：

- VPS URL：`http://<IP>:8765/` — 主用，1 分钟刷新
- GitHub Pages URL：`https://Zhangchen-bit.github.io/newsradar/` — 备份，VPS 挂了还能看（10-30 分钟刷新）

或者你也可以去 Repo Settings → Pages 直接关掉，省得 cron 失败造成混乱。

---

## 7. 故障排查

| 现象 | 检查 |
|---|---|
| 访问 IP:8765 超时 | 阿里云控制台防火墙是否开了 8765？`systemctl status newsradar-web` 是否 active？ |
| 页面打开但快讯流空 | `cat /opt/newsradar/static_public/news.json` 看是否有内容；`journalctl -u newsradar-export -n 30` 看采集报错 |
| cls 一直失败 | 国内 VPS 应该能通；如果是海外 VPS，cls 可能拒境外 IP（可接受，只用 jin10+wscn） |
| 内存爆了 | 1G 内存太小会 OOM，换 2G 套餐 |
| 重启后服务没起来 | `systemctl is-enabled newsradar-export newsradar-web` 应该都是 enabled |

---

## 8. 估算总成本

| 项 | 一次性 | 月度 |
|---|---|---|
| 阿里云轻量 2C2G3M | - | ¥30 |
| 域名（可选） | - | ¥4（年付 50） |
| HTTPS 证书 | - | 0（Caddy 自动） |
| 流量（约 1000 PV/天） | - | 0（套餐内） |
| **合计** | 0 | **¥30-34/月** |

对比 GitHub Pages（免费但 cron 不稳）—— 这 30 块买的是**稳定 + 实时性 + 控制权**。
