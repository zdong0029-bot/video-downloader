# 作者主页和合集下载验收

主页识别与单视频下载是两项能力。只取得作品列表或播放地址，不能标记完整下载通过。

## 当前流程

1. 在原首页粘贴作者主页或合集，自动路由到列表识别。
2. 原生列表解析可用时使用原生解析。快手、小红书、AcFun、好看、芒果、央视、爱奇艺和搜狐读取官方作者/选集页面的真实作品；B站、腾讯和TikTok在原生列表失效时有官方页面补充。
3. 只接收属于目标作者/合集的作品，排除推荐视频、没有官方播放参数的占位链接和主页自身。小红书保留官网原始路径与播放参数，按作品ID去重。
4. 官网页面未确认分页结束时明确标记部分列表；TikTok官方作者嵌入最多只返回最近10条，不能表示全部作品。
5. 勾选后仍使用原来的单视频/原图下载器。保持现有分辨率、帧率、码率排序、原编码合并和完整文件校验。低清历史源不放大冒充高清。
6. 官网安全验证、账号未被接受、无可用流、加密和CDN失败都保留实际错误。单视频可用不代表全主页可用。

## 可重复的真实验收

在下载器主机的 `web` 目录，用其 Python 环境执行：

```powershell
.\.venv\Scripts\python.exe tests\audit_profile_downloads.py --output C:\DownloadAudit --platform Bilibili --platform Xiaohongshu
```

默认清单是 `tests/profile_samples.json` 中的公开样例。它包含旧样例及故障对照，不保证所有入口现在仍有效。用 `--manifest` 指定自己的公开链接清单，格式为：

```json
[{"platform":"Example","url":"https://platform.example/public-author"}]
```

只读列表、不启动媒体下载：

```powershell
.\.venv\Scripts\python.exe tests\audit_profile_downloads.py --output C:\DownloadAudit --metadata-only --platform Kuaishou
```

`--limit 100` 是默认抽样；还可用200或0（请求完整列表）。完整列表用 `--catalog-timeout 1200` 给较长分页时间，但平台未确认结束仍是部分列表。完整下载默认期限900秒，用 `--download-timeout` 修改，超时只取消程序自己创建的任务。最多同时两个独立访客。

完整通过必须包含：该主页真实作品列表 → 勾选一个实际作品 → 主机完整成品 → 访客接口全部字节 → 与主机SHA256一致 → ffprobe媒体信息 → 开头/中间/结尾解码（短MPEG完整顺序解码）→ 另一访客404。仅 metadata-only 返回 listed，不是 passed。

需在PATH中有FFmpeg/ffprobe，并在下载主机运行，才能比较磁盘与访客下载哈希。脚本不会导出管理员凭据或读取其他访客任务；它自己创建的记录保留供检查。输出目录可能包含访客会话、作品播放参数和测试记录，必须放在仓库及static目录以外，不应公开发布。脚本导入时不发起网络请求。

本地接口全文件验收不能替代朋友异地公网全文件验收。测试公网可指定 `--base https://你的公网域名`，仍应由下载主机执行主机文件比对，并另行记录公网速度。速度统计包含解析、下载、合并与校验，不是纯线路峰值。

## 隔离回归

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_profile_catalog.py
.\.venv\Scripts\python.exe tests\verify_batch_catalog.py
```

后者使用临时源码副本和本地媒体，不复制授权文件，不操作生产记录；检查单视频分组、多作品独立卡片、245条主页只下载勾选项、展开收起、定位、并发、访客隔离、移除保留文件和重启恢复。

来源：[TikTok官方作者嵌入范围](https://developers.tiktok.com/docs/en/embed-creator-profiles)、[yt-dlp主页解析](https://github.com/yt-dlp/yt-dlp)。
