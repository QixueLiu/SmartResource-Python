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

## 小说书架自定义名称与进度保护（0.21）

`PATCH /api/shelf/{book_id}/title` 接受 `{"title":"我的小说名"}`（需要 X-API-Key），只修改私人小说书架的显示名称，不修改书籍 URL、上次阅读章节、进度或阅读记录时间。再次从搜索或原网页加入同一本书时，手动改过的名称不会被网页原标题覆盖；未手工改名的条目仍允许正常更新名称。

现有 SQLite 数据自动迁移并补充 `title_custom` 标记（默认 0），不重建小说书架。回归测试覆盖旧数据迁移、重命名合法性、重复收藏、进度保持、权限与不存在的作品。无需视频下载、AI、网络抓取或新中间件。

## 内置浏览器一键收藏小说（0.20）

`POST /api/shelf/quick-save` 接受用户主动选择的 `{"url":"https://小说站/书籍详情页","title":"作品名称"}`（需要 X-API-Key）。仅保存用户指定的书籍详情/目录 URL，不访问网页、不抓取正文、不自动扫描网站、也不调用 AI。若域名尚未加入来源，则自动建立一个未配置搜索规则的 `novel` 类型站点；已有非视频来源则复用，保留其搜索和章节规则；明确的视频来源或已停用站点不会被悄悄更改。

同一作品重复收藏不会覆盖既有作品名、最近章节、阅读百分比或记录时间；直接从浏览器收藏时不保证页面本身能被解析为目录或正文，之后仍以原网站的实际页面结构为准。拒绝站点首页、私有地址、危险协议、非法端口和无效名称。

## 视频收藏改名与最近网页记录管理（0.17）

无需新中间件或额外表：`PATCH /api/videos/shelf/{video_id}/title` 使用 `{"title":"自定义名称"}` 修改收藏名；之后再次收藏相同作品不会覆盖这个手工名称。原有数据自动补充 `title_custom` 标记（不清空书架或播放页）。

`DELETE /api/videos/shelf/{video_id}/last-page` 只清除该收藏的上次访问网页记录（将其恢复为作品详情 URL），不删除收藏、不清理网站。两种接口均受已有 API Token 保护，无效或不存在的收藏返回相应错误。记录同一个页面时不重复变更 `updated_at`。

时间戳代表服务端保存网页记录的时间，不等于用户实际播放时长；不尝试控制原网站播放器或获取媒体时间轴。

## 内置浏览器一键视频收藏（0.16）

Android 内置浏览器访问任意已加载的公开 HTTP(S) 视频页面，点击「收藏当前页为视频」即可加入私人视频收藏，不要求该网站预先配置搜索表单。通过 `POST /api/videos/quick-save`（需 X-API-Key）传递 `{"url":"https://视频站/具体作品","title":"网页标题"}`。仅在用户点击时保存；不抓取视频或启动任何自动站点扫描。

Python 按域名复用现有来源，保留已经配置的搜索和目录规则；尚未添加来源时自动新建 `kind=video` 的网站记录（搜索规则留空、扫描状态标记为 skipped）。不偷偷启用已停用站点，也不会将明确的小说站改为视频站。重复收藏同一页面不丢失已保存的上次观看网址，私人地址、错误协议和站点首页会被拒绝。服务器响应会携带 `created_site` 供 APP 展示提示。

这是网页收藏与继续浏览功能，不代表视频已经实际播放；不提取真实播放进度或媒体资源。

## 视频详情页快捷选集（0.15）

视频收藏新增 `GET /api/videos/shelf/{video_id}/episodes`（需要 Token）：只读取该收藏作品详情页一次公开 HTML，在最多 7 秒内保守识别「第 N 集」「Episode N」或明确播放列表内的数字链接，最多返回 80 条。只保留同域名网页链接，不提取媒体流、不自动播放、不运行 Playwright 或 Ollama。识别结果属于候选，不保证剧集、播放线路完整或可用。

Android「收藏 → 视频 → 选集」展示原生集数列表，点击后仍由 WebView 打开原网站页面并沿用上次访问页面记录。无法识别或网站阻止抓取时，直接使用「原网页选集」即可观看，不影响原来的「继续观看」。

## 视频收藏与上次观看页面（0.14）

新增简洁的视频收藏 API，与小说书架并列，继续使用同一个 FastAPI Token 和 SQLite：

- `GET /api/videos/shelf`：读取收藏和最近打开页面。
- `POST /api/videos/shelf`：保存 `{"site_id":1,"title":"作品名","video_url":"https://视频站/详情"}`，重复收藏不会清空上次观看记录。
- `PUT /api/videos/shelf/{id}/last-page`：记录同一站点内的最新观看页面网址。
- `DELETE /api/videos/shelf/{id}`：移除收藏。

仅保存网站 URL、作品名称和时间，不下载或代理任何视频流，不尝试绕过 DRM、付费或反爬措施。WebView 页面跳转不一定代表实际播放，故目前记录的是**上次打开的网页**而非精确播放时长。无法判断正在播放的集数时，用户仍可在原站自行选择。

## 离线可验证的搜索相关性（0.24）

不需要部署任何真实网站即可运行 `pytest -q` 验证离线 HTML 样本。本轮加强了自有站点搜索结果的真实性检查：已配置 CSS 的站点不再把搜索页全部推荐链接默认当作目标作品；仅保留**标题命中**或**同一结果条目的介绍/作者文本命中**的候选。标题精确匹配优先展示，标题包含关键词其次，介绍或作者文本命中属于较低确定性候选；完全无关的推荐和导航链接被排除。

中文书名比较会忽略全半角差异、空格和常见标点，并能识别《书名》免费阅读等带网页装饰标题。保留《书名》第二部等续集和不同作品作为*标题相关*而不是错误标成完全相同。结果包含 `match_grade=exact|title|context`；CSS 规则失效时沿用谨慎的关键词链接回退，明确标记 `extraction=heuristic`。这属于启发式匹配证据，不代表已经打开作品详情页验证，也无法保证任何网站搜索的全面召回。

测试通过人工构造的 HTML，包括噪音推荐、外站链接、重复 URL、简介/作者命中、标题装饰符、旧 CSS 回退和空结果；真实网站仍需要部署后验收。没有新增 OCR、向量数据库、大模型推理、外部接口或长期关键词存储。

## 单人检索优化（0.23）

`GET /api/search?q=作品名称&mode=fast|full&kind=all|novel|video&refresh=false|true`

- 默认快速模式：优先从**已配置站内搜索入口**且健康度较好的站点选择最多 24 个，8 秒总预算、单站 6 秒、并发上限 8；未配置搜索入口的网站不再挤占检索名额
- 可选全面模式：扩大到最多 96 个已配置来源，26 秒总预算、并发上限 16；这是扩大搜索覆盖面，不是承诺检索整个互联网或所有网站
- 90 秒内相同关键词、来源配置、搜索类型和模式优先返回进程内缓存，最多保留 32 组聚合结果与 160 组单站结果；从快速切换到全面时可复用已搜索站点，集中补查新的来源。`refresh=true` 强制重新检索，跳过两级缓存。缓存只存内存，不需要 Redis、不持久化用户关键词
- 显示 `searchable_sites`、`completed_sites`、`unconfigured_sites`、`skipped_sites`、`timed_out_sites`、`cached`、`elapsed_ms`，明确说明本次检索覆盖情况。未配置搜索规则或超时的站点不冒充已搜索成功
- 标题完全一致的作品优先展示，其次是名称前缀或包含关键词的结果；同一网页 URL 的锚点差异、末尾斜杠差异去重，但不同来源的同名作品仍分别保留供选择
- `kind` 可按小说/视频缩小搜索范围；`mixed` / `other` 来源保留为未确定类型的候选，不伪装成已经确定的小说/视频

快速与全面检索默认均不启动 AI，维持现有源网站安全访问、超时、原网页阅读/观看行为。来源本身没有公开搜索接口或站点要求登录/验证时仍可能没有结果。

## 快速站点检查与有界搜索（0.12）

保持简洁和速度优先：`POST /api/sites/{id}/quick-check` 在用户主动点击时执行，限定总共8秒，只使用普通 HTTP、站点首页一次和最多一次站内样本试搜，**不启动 Ollama 或 Chromium**。只有样本搜索匹配到原作品链接才自动补全原本缺失的搜索 URL。原来已有的搜索 URL 永远不会被静默覆盖；如发现不同且验证通过的候选，只返回 `candidate_template` 供 Android 端用户确认。此验证只是单个样本，不证明所有内容可用。

跨站搜索并发上限8，单站6秒，整次8秒，最多查排名前24个站点。未查的数量以 `skipped_sites` 报告，超时站点在 `warnings` 中说明，不声称已经查遍所有网站；站点配置保留。现有本地 AI 仍可手动启用，**搜索和快速检查不会自动调用 AI**。

## 书源健康度与可靠性排序（0.11）

Python 在服务端为已添加网站记录最近 30 天、每站最多 150 条的搜索/阅读观测事件。只保存站点 ID、操作类别、成功/失败/中性结果、标准错误类型和时间；不记录搜索关键词、作品标题、网页 URL、Token 或正文。删除站点时同步清理该站事件。

- `GET /api/sites` 增加每站 `health`（score、status、samples、successes、failures、neutral）。`GET /api/sites/health` 提供全部站点汇总，`GET /api/sites/{id}/health` 提供最近 10 条标准化失败原因。
- 成功读取小说章节/目录或通过已配置 CSS 的搜索提取到有效结果记为成功；网络错误和正文提取失败记为失败；**搜索没有结果及启发式搜索命中是中性，不算网站故障或已验证成功**。
- 使用贝叶斯先验计算 0–100 的保守参考分；不足 4 条有效样本标记 `insufficient_data`。不因一次成功把网站当成百分之百可靠。
- 跨站搜索与备用书源发现根据历史观测分排序，优先尝试近期表现较好的站点；低分站点仍参与搜索，不自动停用，也不擅自切换阅读来源。
- 系统只评估本机最近实际请求的成功程度，不代表全网可用率、正版版权、内容质量、更新速度或不同作品章节一致性。

## 小说备用书源自动发现（0.10）

新增 `POST /api/reader/alternatives`：传入 `{ "book_id": 1, "chapter_title": "第十二章 风起" }`，服务端从自己的私有书架读取作品名称，最多检查 6 个已配置且启用的其他小说网站；可以用 `max_sites` 调整为 1–8。

发现流程：从其他站点搜索作品标题 → 要求归一化后的作品名称完全一致且只有一个命中 → 自动读取作品目录（最多 2 页）→ 按章节编号和有效标题匹配唯一章节 → 实际提取该章节正文确认可阅读 → 返回候选、来源和匹配证据。不会改书架、绕过登录或把新站偷偷设置成默认书源。

返回 `status=matches/no_match/needs_chapter_title`、`alternatives`、`warnings`。候选 `confidence=exact_chapter` 表示作品名、章节编号、章节标题及单页正文提取都通过检查；`number_only` 表示仅章节编号匹配，不能保证章节正文相同。**即使是 exact_chapter，也只代表有限证据，不保证不同站点内容完全一致**。

未能获取目标章节编号时不会乱猜（例如仅有“下一章”按钮，而没有章节标题）。Android 可以在章节失败时自动调用本接口，展示候选，待用户确认后再从新网站打开；不会自动替换原书架记录。视频功能不受影响。

## 小说目录自动翻页（0.9）

`POST /api/reader/open` 在识别出小说目录时，默认沿着明确标注的 **目录下一页** 继续读取并合并章节，最多 3 个目录页面（包括第一页），只读取同一网站域名并根据章节 URL 去重。不会把“下一章”当作目录翻页，也不会自动下载正文。

可传 `max_toc_pages` 调整为 1–5；返回 `pages_fetched`、`has_more_directory_pages`、`next_directory_page` 及 `directory_warnings`。超过上限时明确标记仍存在后续目录，不声称全书完整。站点使用复杂 JS 分页、无限滚动或不同分页标签时仍可能遗漏。

## 小说书架与跨设备阅读进度（0.8）

增加由自建 FastAPI Token 保护的私有书架。数据保存在 Python 服务端 SQLite，不上传第三方云服务；同一服务地址和 Token 可以让不同 Android 设备看到同一记录。

- `GET /api/shelf`：按最后更新时间查看已收藏作品、所属站点、最后章节和阅读百分比。
- `POST /api/shelf`：正文 `{"site_id":1,"book_url":"https://books.example.org/book/1","title":"作品名称"}`，首次收藏或重复打开；同一站点 + 原始作品 URL 唯一，重复添加不覆盖进度。
- `PUT /api/shelf/{id}/progress`：`{"chapter_url":"https://books.example.org/chapter/5","chapter_title":"第5章","progress":0.42}`。百分比范围 0–1，章节必须和原作品网站同域名。
- `DELETE /api/shelf/{id}`：从书架中删除，需在 Android UI 中确认。

Android 搜索结果点击“阅读”会自动添加到书架；以后可以在“书架”中继续上次章节。阅读滚动过程中防抖同步百分比；阅读器仍在手机本地保存显示设置。服务端不储存整章正文或版权内容。

限制：这是单用户私有服务器方案，共用同一个 API Token 的客户端共享书架，目前没有多账号权限隔离和跨设备冲突合并。禁用或删除网站后保留书架元数据，无法通过已禁用站点读取新章节。当前未实现离线缓存。

## 本地 Ollama AI 规则分析（0.7）

本地 AI 模块默认关闭，不依赖云端 API。RTX 3050 8GB 主机可以先尝试一个轻量模型：

~~~bash
ollama pull qwen2.5-coder:3b
ollama list
export SMARTRESOURCE_AI_ENABLED=1
export SMARTRESOURCE_OLLAMA_MODEL=qwen2.5-coder:3b
export SMARTRESOURCE_OLLAMA_URL=http://127.0.0.1:11434
~~~

Linux 使用 export；Windows PowerShell 使用 $env:SMARTRESOURCE_AI_ENABLED='1' 等。启动 FastAPI 前也必须设置保密的 SMARTRESOURCE_API_TOKEN。

- GET /api/ai/status：返回配置状态和模型名称（不代表模型已成功加载）。
- POST /api/sites/{id}/ai-analyze：请求 {} 或 {'page_url':'https://同站网页'}，只分析已添加且启用的同域名网页。
- Python 从 HTML 中提取无脚本、无输入值和无站外链接的精简结构，交给本机 Ollama 的 /api/generate JSON 模式。
- AI 只建议搜索结果和章节目录的 CSS，服务器必须对当前网页验证选择器和同域名链接，否则丢弃。
- 模型推理单进程串行；不会执行模型生成的代码，也不会保存模型提出的候选规则；用户在 Android 端检查并明确保存。
- 不能解析登录、验证码、付费和 DRM；无法保证单页面验证代表全站可用。

**安全要求**：只允许 localhost:11434。不要将 Ollama 11434 端口暴露到公网、LAN 或 Tailscale；Android 只访问需认证的 FastAPI。网页及其文字属于不可信输入，本地模型可能给出错误建议，必须以验证结果为准。

## 小说阅读模式（0.6）

`POST /api/reader/open`，传入：

```json
{"site_id":1,"url":"https://已添加小说站.example/chapter/1"}
```

接口只抓取**已配置站点下的单个公开 HTML 页面**，返回三种情况：

- `kind=chapter`：从可识别的文章容器中提取清理后的纯文本段落、章节标题、原网页链接及同站上一章/下一章的候选地址。APP 用 Kotlin Compose 显示原生阅读界面。
- `kind=toc`：这是书籍详情/目录页；返回当前页面识别到的章节链接，点击章节再读取。
- `kind=fallback`：提取证据不足，显示打开原网页的入口，不会把导航或登录页冒充正文。

内容只在请求时处理并返回，不对整个网站进行批量下载；也不会自动缓存大量章节。不保证所有小说站都能提取，无法访问或权限受限时保留原网页。章节的“下一章”链接是启发式匹配，需要根据具体站点核查。不会绕过登录、付费或验证机制。

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
