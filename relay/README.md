# 国内中转服务器（原文件加速分发）

## 它解决什么问题

朋友现在经 Tailscale Funnel 从家里电脑下载原文件，实测只有约 0.2 MiB/s（见 `deploy/PUBLIC_DOWNLOAD_DELIVERY.md`）。
开启中转后流程变成：

1. 家里电脑下载完成 → 后台把**原文件**分段上传到你租的国内服务器（8 MiB 一段，断点续传，SHA-256 校验）。
2. 网页文件列表显示“云端同步 xx%”，完成后显示“云端加速可用”。
3. 朋友点下载 / 分段保存 / 存相册时，网页先向家里电脑要一个 **6 小时有效的签名链接**，然后直接从国内服务器接收同一份原文件。
4. 中转未完成、失败或未配置时，自动回退到原来的家里电脑传输，行为与以前一致。

不转码、不压缩，朋友收到的文件与家里电脑上的逐字节相同。

**要知道的限制（不夸大）：**

- 第一次同步仍受家里上行带宽限制：约 28 Mbps ≈ 3.3 MiB/s，6 GB 文件大约要 30 分钟以上才能同步完。
- 服务器标称 200 Mbps 峰值 ≈ 23.8 MiB/s，这是**所有朋友共享**的总出口，实际速度还取决于朋友所在网络。上线后请用真实手机/电脑实测。
- 文件在服务器上保留 7 天后自动删除；磁盘不足时服务器会拒绝新上传（HTTP 507），网页继续走家里电脑。
- 签名链接只能读取那一个文件，6 小时后失效；网页会在过期时自动重新申请（重新申请时家里电脑会再次检查访客所有权）。

## 你需要准备 / 提供

| 项目 | 说明 |
| --- | --- |
| 一台阿里云国内轻量应用服务器 | Linux（推荐 Ubuntu 22.04/24.04 或 Debian 12），2 核 2G 即可。记下**公网 IP**。 |
| 访问方式二选一 | **已 ICP 备案的域名**（最稳），或**直接用公网 IP**（见下文“没有备案域名”）。 |
| 两个随机密钥 | `RELAY_UPLOAD_TOKEN`（上传令牌）和 `RELAY_SIGNING_KEY`（签名密钥），**服务器和家里电脑填同样的值**。 |
| 网站地址 | 朋友打开的网址（Funnel 网址，如 `https://xxx.ts.net`），填到 `RELAY_ALLOWED_ORIGIN`。 |
| 防火墙 | 在阿里云控制台“防火墙”放行 TCP **80** 和 **443**（SSH 的 22 默认已放行）。 |

> 域名说明：解析到中国大陆服务器的域名，要先完成 ICP 备案才能用 80/443 正常访问，未备案域名会被拦截。
> 直接用 IP 访问不涉及域名，但阿里云/运营商的政策可能调整，不能保证永久可用。

生成密钥（在任何装了 Python 的电脑上运行两次，得到两个不同的值）：

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(32))"
```

请自己保管好，不要发到群里、不要提交到 Git。

## 服务器安装步骤（复制粘贴即可）

以下命令用 `root` 或带 `sudo` 的账号，通过阿里云控制台的“远程连接”或 SSH 执行。

### 1. 安装基础软件

```bash
sudo apt update
sudo apt install -y python3 python3-venv debian-keyring debian-archive-keyring apt-transport-https curl
# 安装 Caddy（自动申请 HTTPS 证书的网页服务器），官方源：
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
caddy version
```

（若服务器访问 cloudsmith 很慢，可在阿里云镜像或 Caddy 官网下载对应 Linux 二进制，放到 `/usr/bin/caddy`。）

### 2. 放置中转程序

把仓库里的 `relay/` 目录上传到服务器（可用 `scp`、WinSCP 或控制台的文件上传），然后：

```bash
sudo useradd --system --home /opt/vd-relay --shell /usr/sbin/nologin vdrelay
sudo mkdir -p /opt/vd-relay /var/lib/vd-relay
sudo cp server.py requirements.txt /opt/vd-relay/
sudo python3 -m venv /opt/vd-relay/.venv
sudo /opt/vd-relay/.venv/bin/pip install -r /opt/vd-relay/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
sudo chown -R vdrelay:vdrelay /opt/vd-relay /var/lib/vd-relay
```

### 3. 填写密钥

```bash
sudo cp deploy/relay.env.example /etc/vd-relay.env
sudo nano /etc/vd-relay.env        # 填入两个密钥和网站地址，Ctrl+O 保存，Ctrl+X 退出
sudo chmod 600 /etc/vd-relay.env
```

### 4. 开机自启

```bash
sudo cp deploy/vd-relay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vd-relay
systemctl status vd-relay --no-pager      # 看到 active (running) 即成功
curl http://127.0.0.1:8080/healthz        # 返回 {"ok":true,...}
```

### 5. 配置 HTTPS（Caddy）

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
```

- **有备案域名**：把 `relay.example.cn` 改成你的域名，并在域名解析里加一条 A 记录指向服务器公网 IP。
- **没有备案域名**：删除方案 1 整段，把方案 2 前面的 `#` 去掉，`203.0.113.10` 换成你的公网 IP。

然后：

```bash
sudo systemctl reload caddy
journalctl -u caddy -n 50 --no-pager      # 看到 certificate obtained successfully 即证书申请成功
```

在自己电脑浏览器打开 `https://你的域名或IP/healthz`，能看到 `{"ok":true,...}` 且地址栏是锁形图标即可。

#### 没有备案域名：Let's Encrypt IP 证书（注意事项）

- Let's Encrypt 从 2026 年 1 月起正式签发 IP 地址证书，但**只能是 6 天左右的短期证书**（`shortlived` 配置），
  且只能用 HTTP-01 / TLS-ALPN-01 验证（所以 80/443 必须对外开放）。来源：
  https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability
- Caddy 会自动续期，但续期依赖 Let's Encrypt 从境外访问你的服务器 80 端口；跨境网络不稳时可能续期失败，
  证书过期后网页会自动回退到家里电脑传输。请偶尔看一眼 `journalctl -u caddy`。
- 较旧版本的 Caddy 会拒绝为公网 IP 申请证书；IPv6 地址的支持在较新版本才修好。请用最新稳定版。
  相关讨论：https://github.com/caddyserver/caddy/issues/7399
- 如果 Caddy 始终申请不到，可改用 acme.sh（`--certificate-profile shortlived`）申请后让 Caddy 读取证书文件，
  参考 https://m.ac/letsencrypt-ip-certificate/ 。
- 本仓库提供的 Caddyfile 已用 Caddy 2.10.2 做过语法检查，但**没有在真实阿里云服务器上实际签发过证书**，请以你服务器上的日志为准。
- 不要用自签名证书：浏览器会拒绝网页跨域读取，朋友无法从中转下载。

### 6. 清理（已内置）

服务进程每小时自动删除超过 7 天的文件和超过 2 天未完成的上传分段。也可以手动或用 cron 执行：

```bash
sudo -u vdrelay env $(sudo cat /etc/vd-relay.env | xargs) /opt/vd-relay/.venv/bin/python /opt/vd-relay/server.py --cleanup
```

## 家里电脑（Windows）配置

任选一种方式，填写与服务器**相同**的两个密钥：

**方式 A：配置文件（推荐）** —— 新建 `web\data\relay.json`（`web/data/` 已在 .gitignore 中，不会进 Git）：

```json
{
  "base_url": "https://你的域名或公网IP",
  "upload_token": "与服务器 RELAY_UPLOAD_TOKEN 相同",
  "signing_key": "与服务器 RELAY_SIGNING_KEY 相同",
  "concurrency": 1
}
```

**方式 B：环境变量** —— `RELAY_BASE_URL`、`RELAY_UPLOAD_TOKEN`、`RELAY_SIGNING_KEY`（可选 `RELAY_CONCURRENCY`=1 或 2）。
环境变量优先于配置文件。

保存后重启下载器服务。三项缺任何一项，中转功能完全关闭，网页与以前完全一样。

说明：
- 上传**直连**中转服务器，不经过系统代理或家里给 TikTok/YouTube 用的代理。
- 同时最多上传 1–2 个文件（默认 1 个，避免占满家里上行影响其他下载）。
- 上传中断会从服务器已收到的位置续传；连续多次失败会标记“云端同步失败”，朋友点下载时会重新排队。
- 已有的旧文件不会自动全部上传；朋友对某个文件点下载时才会开始同步该文件。
- 在家里电脑或局域网打开网站时不使用中转（本地更快）。管理员把“视频传输”切到“主机传输”也会关闭中转。
- 删除电脑上的文件（“删除文件”）会同时请求服务器删除中转副本；“移出列表”不会。

## 接口说明（给维护者）

| 接口 | 用途 |
| --- | --- |
| `HEAD /upload/{key}` + Bearer | 查询已收到字节数：`Upload-Offset`、`Upload-Complete`、`X-Content-Sha256` |
| `PUT /upload/{key}` + Bearer + `Content-Range: bytes a-b/total` | 追加一段，必须从当前 offset 开始（否则 409 并返回正确 offset） |
| `POST /complete/{key}` + Bearer + `{"size","sha256"}` | 校验大小与 SHA-256，原子改名发布；不一致返回 422 并丢弃分段 |
| `DELETE /upload/{key}` + Bearer | 删除副本 |
| `GET/HEAD /f/{key}?exp=&sig=` | 签名下载：`sig = HMAC-SHA256(RELAY_SIGNING_KEY, key + "\n" + exp)`；支持 Range/206、If-Range、强 ETag、UTF-8 文件名、CORS |

`key` 形如 `jobs/{设备标识的哈希}/{任务ID}/{文件名}`，拒绝 `..`、反斜杠、控制字符等路径穿越写法。

测试：`cd relay && python -m pytest -q tests`
