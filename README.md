# SmartResource Python

自托管 FastAPI 站点目录、聚合搜索和小说目录初步分析。**不依赖第三方小说/影视聚合接口**。目前仅适配提供明确站内搜索与目录 CSS 规则、可公开读取的 HTML 网站；网站改版、验证/付费、JS 动态渲染与目录分页等暂不支持。

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
