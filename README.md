# SmartResource Python

自托管 FastAPI 站点目录、聚合搜索、自动站点配置与小说目录初步分析。**不依赖第三方小说/影视聚合接口**。目前仅适配提供明确站内搜索与目录 CSS 规则、可公开读取的 HTML 网站；网站改版、验证/付费、JS 动态渲染与目录分页等暂不支持。

## 快速启动 (Python 3.11+)

\`\`\`bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
# Windows PowerShell: $env:SMARTRESOURCE_API_TOKEN="choose-a-long-random-token"
# Linux: export SMARTRESOURCE_API_TOKEN="choose-a-long-random-token"
uvicorn smartresource.main:app --host 127.0.0.1 --port 8000
pytest -q
\`\`\`

将 \`SMARTRESOURCE_API_TOKEN\` 设置为随机、足够长的值；不设置则仅健康检查可用，其他 API 返回 503。Android APP 在设置页填写 Token。需要手机外网访问时，优先通过 Tailscale；仅允许 Tailscale 网络访问该端口，**切勿把 8000 直接暴露公网**。

## 自动搜索样本验证和动态网页分析（0.5）

导入网站后，若发现同域名 GET 搜索表单，会尝试从首页作品链接选择一个标题作为正向样本，在原站搜索并检查是否返回对应作品 URL。通过则标记为 `probe_passed`；未找到样本或不匹配则仍是 `candidate`。**这是单次样本检查，不代表全站搜索正确。** 已有人工规则不会被覆盖。

`POST /api/sites/{id}/verify-search` 可以重新检查单个站点。

Playwright 是可选模块，默认不开启。若要对可信站点尝试 JavaScript 渲染：

```bash
pip install -e '.[browser]'
python -m playwright install chromium
# Linux: export SMARTRESOURCE_ENABLE_BROWSER=1
# Windows PowerShell: $env:SMARTRESOURCE_ENABLE_BROWSER='1'
```

仅对静态抓取失败或明显的 JS 空壳尝试 Chromium；限制同域名请求、禁止下载和媒体资源、禁用 Service Worker。网页依赖跨域 CDN 时可能无法正常渲染。动态执行第三方 JavaScript 具有风险，务必只在隔离、低权限环境中为可信站点开启。此限制不能保证防止所有 DNS rebinding 和网络旁路，不应部署到高信任内网。不得绕过登录、反爬验证或 DRM。

## 自动网站配置（0.4）

添加网站或批量导入网址后，服务器默认在后台**自动扫描新网站的公开 HTML 首页**，不再需要逐个点击「分析」并填写搜索地址。

- 自动识别：站点大致类型、HTML GET 搜索表单、站内搜索 URL（保留 `{q}` 占位符）。
- 当网站原来没有搜索 URL 时，仅保存**根据 GET 表单生成的未验证候选搜索地址**。不会覆盖已有的人工规则，也不会冒充已验证成功。
- 搜索结果未配置 CSS 时，保留原有「标题包含关键词的同域名链接」回退策略；误报和漏报可能出现。
- 目录对比遇到没有 CSS 规则的网站，先在**当前作品目录页**寻找章节列表，临时使用候选规则分析，不要求先手工保存。
- 使用 `GET /api/sites` 的 `scan_status`、`scan_message`、`scan_checked_at` 查看扫描状态。
- `POST /api/sites/scan` 请求体 `{"limit":100}` 可重新扫描未配置/失败站点；或用 `{"site_ids":[1,2]}` 指定网站。
- 扫描限同一时刻 2 个公开网页请求、禁止跟随 HTTP 重定向。数据库保留状态，服务重启后 `queued/running` 状态会重置为待处理；需再触发扫描。

要禁止新增时的自动抓取，可在 `POST /api/sites`、`/api/sites/import` 或 `/api/sites/import-text` 使用查询参数 `auto_scan=false`。

此功能**不是通用爬虫或 AI 自动修复**。站点采用 POST/JS 搜索、反爬验证、登录或特定页面规则时，仍需要后续 Playwright/专属适配器。服务器只分析用户主动导入并允许访问的站点；请求限制不等于所有网站都允许抓取，应遵守站点政策。

## 新增：网页结构检测（0.3）

`POST /api/sites/{id}/analyze`（需 X-API-Key）

请求 `{}` 检测网站首页，或 `{"page_url":"https://已配置域名/book/123"}` 检测单个目录页面。

返回 `kind_guess`（小说/视频/混合/未知）、`search_candidate`（仅根据 GET 表单推断站内搜索 URL）、`toc_candidate`（根据章节链接推断目录 CSS）、`notes`。**仅返回候选规则，不会写入数据库或自动修改已有配置。**

Android APP 可点击「分析」，检查候选后应用到编辑表单，最终由用户明确保存。网站有站内 GET 搜索模板但未配置结果 CSS 时，服务端会对搜索响应尝试同域名关键词链接匹配，并将结果标记为 `heuristic`；误报和漏报均有可能。POST 表单、JS 渲染、站外跳转及访问限制均不自动绕过。

## API

| API | 说明 |
| --- | --- |
| \`GET /api/health\` | 服务状态（无需 Token） |
| \`GET /api/sites\` | 全部网站列表 |
| \`POST /api/sites\` | 添加网站，支持搜索和目录 CSS 规则 |
| \`PATCH /api/sites/{id}\` | 编辑分类、配置、启用状态 |
| \`DELETE /api/sites/{id}\` | 删除网站 |
| \`POST /api/sites/import-text\` | 按行批量导入 URL 或 “名称 | URL” |
| \`POST /api/sites/import\` | 批量导入配置对象 |
| \`GET /api/search?q=xxx\` | 并发搜索，返回各网站结果和诊断 |
| \`POST /api/novels/compare\` | 比较 1–8 个候选目录的章节数量、疑似缺章 |

以上除健康检查外都须传入 HTTP Header \`X-API-Key: ...\`。

### 批量网址导入

\`\`\`json
{"kind":"novel","text":"示例书站 | https://books.example.com\nhttps://another.example.org"}
\`\`\`

只入库网站域名并去重。这里“小说”只是用户给的标签，**没有自动识别站点类型，也没有自动猜测搜索规则**。

### 搜索与目录规则

\`\`\`json
{
  "name": "示例书站",
  "base_url": "https://books.example.com",
  "kind": "novel",
  "search_url_template": "https://books.example.com/search?q={q}",
  "item_selector": ".search-result",
  "title_selector": "a.book-title",
  "link_selector": "a.book-title",
  "toc_item_selector": "li.chapter",
  "toc_title_selector": "a",
  "toc_link_selector": "a"
}
\`\`\`

示例网站和选择器均为虚构；需要按真实网站验证。网站缺少搜索规则时可在 Android 原浏览器打开，不会在聚合搜索自动出现。

对比接口请求示例：

\`\`\`json
{
  "candidates": [
    {"site_id": 1, "url": "https://books.example.com/book/123"},
    {"site_id": 2, "url": "https://other.example.org/book/123"}
  ]
}
\`\`\`

**目录分析当前只抓取单个公开 HTML 页面**，不自动翻目录分页、访问正文或证明“完结/全本”；同时没有反爬绕过、跳转自动处理或 AI 修复。评分是相对参考，不是内容完整性的保证。

## 安全边界

- 不允许配置本地/内网地址作为抓取目标；抓取前再解析域名以拒绝内网解析。
- 搜索与目录抓取不跟随 HTTP 重定向，不使用操作系统代理环境变量，限制请求大小、超时与并发。
- DNS rebinding、前置代理与跨域 CDN 等复杂场景无法被此 MVP 完全覆盖，所以**只对受信任的用户开放站点配置**，并运行在 Tailscale/localhost 私有网络。
- 不绕过登录、验证码、访问付费、地域或 DRM 限制；视频使用 Android WebView 原播放器。

## 下一阶段

Playwright 可选 JS 页面检测、目录分页适配器、收藏/阅读进度、AI 辅助失效规则分析、站点健康度监测。
