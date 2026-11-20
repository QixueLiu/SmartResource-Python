# SmartResource Python

Python + FastAPI 的自建私有服务，直接访问用户配置的网站（不依赖第三方小说/影视聚合 API）。

## 当前提供（0.1 初始工程）
- SQLite 保存站点（小说 / 视频 / 混合）和搜索适配配置。
- `GET /api/health`、`GET/POST /api/sites`、`POST /api/sites/import`。
- `GET /api/search?q=关键词`：仅对配有搜索模板 + CSS 选择器的网站发起公开网页搜索、合并去重；返回未配置或请求失败原因。
- 网页访问超时、限制同站请求、基础 URL 校验、禁用跨站重定向；不尝试绕过登录、验证码、付费或 DRM。

**未实现**：自动识别所有网站搜索入口、自动广告过滤、小说目录完整度算法、AI 自动修复、视频流提取。代码已按模块留出扩展位置。

## 安装启动
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e '.[dev]'
# Linux/macOS:
export SMARTRESOURCE_API_TOKEN='your-strong-private-token'
# Windows PowerShell: $env:SMARTRESOURCE_API_TOKEN='your-strong-private-token'
uvicorn smartresource.main:app --host 127.0.0.1 --port 8000
pytest -q
```

要让手机通过 Tailscale 访问，可在确保防火墙只放行私网流量、已设置 `SMARTRESOURCE_API_TOKEN` 后，将监听地址换成 Tailscale 网卡的 IP 或 `0.0.0.0`；不要直接开放公网 8000 端口。

## API 例子
```bash
curl -H 'X-API-Key: your-strong-private-token' http://127.0.0.1:8000/api/sites
```

### 新建站点
```json
{
  "name": "Example book site",
  "base_url": "https://example.org",
  "kind": "novel",
  "search_url_template": "https://example.org/search?q={q}",
  "item_selector": ".search-result",
  "title_selector": "a.title",
  "link_selector": "a.title"
}
```
以上 URL / CSS 纯属格式示例，不保证示例域名真的提供该检索入口。只有完成配置的网站才参与自动搜索；未配置网站仍可在 Android 原网页中打开。

## 后续计划
1. Playwright 动态网站适配器（按需启用，不能绕过访问权限）。
2. 搜索结果归一化与作品实体匹配。
3. 章节目录解析、缺章/重复统计、章节可信度评分。
4. 阅读模式、页面清洗、视频网页索引、网站健康度。
5. RTX 3050 / Ollama 仅用于无法通过规则确定的网页结构分析。
