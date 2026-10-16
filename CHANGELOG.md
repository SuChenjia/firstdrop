# 变更日志（CHANGELOG）

> 项目：FirstDrop · 全网热点聚合与首发溯源（`firstdrop`）
> 用途：记录每一次代码 / 架构 / 数据口径变更，供回溯、复核与审计。
> 更新规则：**每条记录按时间倒序排列，最新变更在最上方。**

---

## 维护约定（每次变更必读）

1. 任何代码、架构、数据口径变更**完成后立即**在「变更记录」顶部追加一条新记录，编号递增。
2. 每条记录固定包含四部分：**变更点 / 涉及文件 / 原因 / 验证方式**。
3. 有序号即代表一次独立变更点，便于逐条引用与核对。
4. 关键数值必须写明 **before → after**（如归一化口径、分类占比、合并结果、接口返回值）。
5. 凡涉及数据库结构变更，必须同时说明迁移方式（`schema.sql` 变更 + `db.py` 迁移逻辑），保证旧库可自愈。
6. 凡涉及服务进程代码变更（尤其新增路由），必须重启服务；文档中注明，避免「前端新、后端旧」错配。

---

## 变更记录

<!-- ▼ 新增记录请插入本行下方（最新一条在最上） -->

### #45 · 2026-10-16 · MCP 接入指南双语化（英文设为默认）

- **变更点**：
  1. **`docs/mcp-setup.md`** 改写为英文版（与 README 英文默认约定一致），覆盖工具表、启动方式、Claude Desktop / Cursor 配置 JSON、使用示例、注意事项；顶部加 `English | [中文]` 互链行。
  2. 原中文版迁移为 **`docs/mcp-setup.zh-CN.md`**，顶部加 `[English] | 中文` 互链行。
  3. `README.md` / `README.zh-CN.md` 的 MCP 章节链接与目录结构树同步指向对应语言版本并互标。
- **涉及文件**：`docs/mcp-setup.md`、`docs/mcp-setup.zh-CN.md`（新增）、`README.md`、`README.zh-CN.md`
- **原因**：README 英文默认后，国际用户顺着 MCP 章节点进全中文文档会断档；MCP 是该项目面向 AI 工具用户的核心卖点，接入文档必须英文可达。
- **验证方式**：两份文档均含互链行；README 双版本链接各指向对应语言；纯文档变更，无代码影响。

### #44 · 2026-10-16 · demo 页话题/溯源列表：解除固定上限 + 分页展开 + 数量徽标

- **变更点**：
  1. **`app/templates/demo.html`**：热议话题请求上限 12 → 100、首发溯源 10 → 200；首屏仍各渲染一页（话题 12 / 溯源 10），超出部分通过「展开全部 N 条 / Show all N」按钮就地展开，再点「收起 / Show less」还原；展开状态在语言切换后保留。
  2. 两个区块标题右侧新增**数量徽标**（`共 N 个 / N total`），让访客直观看到数据规模（当前实际：跨平台话题 17 个、已溯源事件 29 条）。
  3. 渲染逻辑重构：`topicCardHTML` / `traceCardHTML` 纯函数 + `renderTopics` / `renderTraces` + 客户端缓存 `TOPICS_ALL` / `TRACES_ALL`；热度条 maxHeat 改按全量列表计算，展开后刻度一致；i18n 字典新增 `countTotal` / `showAll` / `collapse` 双语键；新增 `.sec-side` / `.sec-count` / `.more-btn` 样式。
- **涉及文件**：`app/templates/demo.html`
- **原因**：此前两个区块数量被前端硬编码 limit 截断（话题 12 / 溯源 10），访客无法感知数据规模，且总量超出部分无处查看；作为公开门面需要展示完整数据深度。
- **验证方式**：数据库实测 `v_cluster` 跨平台话题 17 个、`event_earliest_signal` 已溯源 29 条，确认分页设计有真实内容支撑；接口本身支持 limit 参数无需后端改动；模板热加载，刷新页面即生效，无需重启进程。

### #43 · 2026-10-16 · 英文 README 设为默认 + 英文日报 + 英文界面截图

- **变更点**：
  1. **README 默认语言互换**：原 `README.en.md` → `README.md`（英文成为仓库门面，GitHub 默认渲染英文版）；原中文 `README.md` → `README.zh-CN.md`（GitHub 约定的中文译名）。两文件顶部互链同步为 `English | [中文](README.zh-CN.md)` / `[English](README.md) | 中文`；两份文档的目录结构树同步改为 `README.md` + `README.zh-CN.md`。
  2. **英文每日日报（`brief.py`）**：新增 `STR = {zh, en}` 界面文案字典贯穿 `_render_html` / `_render_md`（标题、统计胶囊、预警区、字段标签、溯源行、页脚、日期格式：中文 `%Y年%m月%d日` → 英文 `%b %d, %Y`）；`_auto_summary` / `_auto_spread` 增加英文规则模板（仅复述数据口径，不虚构）。`generate(date, langs=("zh","en"))` 一次运行产出两份文件：中文 `YYYYMMDD.html/.md`、英文 `YYYYMMDD-en.html/.md`；**LLM 润色仅注入中文版**，英文版无 LLM 时走英文规则模板、配置 LLM 时也不套用中文 LLM 文案（避免中英混杂）。事件标题、平台代码、来源站点名英文版同样保留中文原文。
  3. **demo 页日报按钮按语言取文件**：`loadBrief()` 在中文语言下找 `YYYYMMDD.html`、英文语言下找 `YYYYMMDD-en.html`，命中才显示入口；`setLang()` 切换语言时重新拉取日报入口，保证链接随语言切换。
  4. **三张英文界面截图**（无头 Edge `--lang=en-US` + 全新 profile，利用页面 `navigator.language` 自动判定为 en）：`docs/images/demo-home-en.png`（1440×2150）、`docs/images/event-detail-en.png`（1440×1020，事件 id 已由 1198 变为 3412——scheduler 持续运行后重新聚类，旧 id 接口返回「事件不存在」）、`docs/images/daily-brief-en.png`（1440×1600，`/data/briefs/20261016-en.html`）。英文 README 的三处图片引用全部切换为 `*-en.png`；中文 README 继续引用原中文截图。原三张中文截图保留未删。
  5. 中英 README 的「每日简报」能力条目与 `data/briefs/` 目录注释同步更新为「中英双份 / English: YYYYMMDD-en.html」。
- **涉及文件**：`README.md`（原 README.en.md 改名并改图链/互链/结构树/能力条目）、`README.zh-CN.md`（原 README.md 改名并改互链/结构树/能力条目）、`app/brief.py`、`app/templates/demo.html`、`docs/images/demo-home-en.png`、`docs/images/event-detail-en.png`、`docs/images/daily-brief-en.png`、`CHANGELOG.md`
- **原因**：面向 GitHub 国际受众，仓库默认 README 应为英文；英文 README 中若仍配中文框架截图会造成「文档语言与产品画面不一致」的观感，而日报页此前只有中文版，故同步补齐英文日报渲染（规则模板零外部依赖、零额外 LLM 成本），让三张截图全部来自真实英文页面而非手工制作。
- **验证方式**：
  1. `python -m app.brief` 实跑返回 `files: [{lang: zh, 20261016.html/.md}, {lang: en, 20261016-en.html/.md}]`，event_count=10；`/api/briefs` 列出 `20261016-en.html`（16,286 bytes）。
  2. 英文 HTML 抽检：`<html lang="en">`、标题「FirstDrop Daily Briefing · Oct 16, 2026」、统计胶囊 Active events / Alerts today / High-priority / New traces / Items collected、规则摘要「This topic appeared on N platforms, with peak heat …」、溯源行 First source / Confidence 均正确；事件标题保持中文。
  3. 三张截图逐一人工读图确认：demo 页全英文框架 + 中文话题卡 + 「View today's daily briefing」入口；event 页 Platforms/Topic entries/Best rank/Event ID、PLATFORM RANKING PERFORMANCE 表头全英文、榜单标题中文；英文日报标题、统计、预警区、事件卡片英文、标题中文。
  4. `brief.py` / `demo.html` 属服务代码与模板，web 与 scheduler 均已重启（模板热加载，.py 变更需重启）；scheduler 此后每日 23:30 自动产出中英双份日报。

### #42 · 2026-10-16 · 双语升级（Demo / 事件页 i18n）+ 修复并发 500

- **变更点**：
  1. **`demo.html` 极简 i18n（方案 B：框架英文 + 标题保留中文）**：新增 `I18N = {zh, en}` 字典（约 30 条界面文案）、右上角「中文 / EN」语言切换按钮、按 `navigator.language` 自动判定初始语言（中文环境→zh，其余→en）、选择持久化到 `localStorage.firstdrop_lang`。双语化部分：大标题、副标题、6 项统计卡片标签、区块标题、日报按钮、页脚、峰值热度等元信息标签、加载/空状态；动态时间按语言使用 `toLocaleString(t.locale)`。
  2. **`event.html` 同模式双语化**：页面主标题（事件详情 / Event detail）、返回链接、字段标签（覆盖平台 / 话题条目 / 最佳名次 / 峰值热度）、区块标题（首发溯源 / 各平台榜单表现）、溯源框标签（最早发声点 / 来源 / 时间 / 置信度 / 正文摘要 / 查看快照）、榜单表头（平台 / 标题 / 热度 / 名次）、加载与错误态。
  3. **保留中文原文**：事件标题、平台代码（weibo/baidu/…）、来源站点名在两版语言下均**不翻译**——中文标题本身就是产品价值。
  4. **`README.en.md` 补定位句**：「Track what's trending across the Chinese internet — cross-platform aggregation, origin tracing, burst alerts」，并说明「界面框架支持英文、标题保留中文」；中英 README 的「核心能力」各补一条双语界面说明。
  5. **修复并发 500（真实 bug）**：`web.py` 的 `do_GET` / `do_POST` 在每个请求内都调用 `db.init_db(conn)`，而 `schema.sql` 的派生视图采用 `DROP VIEW IF EXISTS` + `CREATE VIEW` 的**非原子**写法。`ThreadingHTTPServer` 每请求一线程，两个请求的 schema 脚本交错时，后到者执行 `CREATE VIEW` 会撞上「view already exists」抛 `OperationalError`（日志可见 `/api/clusters`、`/api/summary` 间歇 500，直接导致 demo 页数据接口失败）。启动时 `main()` 已调用 `init_db`，故移除请求内的冗余调用即可根治。
- **涉及文件**：`app/templates/demo.html`、`app/templates/event.html`、`app/web.py`、`README.en.md`、`README.md`、`CHANGELOG.md`
- **原因**：GitHub star 潜力评审指出——国际访客需要的不是「翻译过的产品」，而是一个**能看懂框架**的界面（英文导航/标签 + 中文原始标题），这是「China trending tracker」这一稀缺定位的正确打开方式；方案 B 性价比最高，方案 C（全站含管理看板 i18n）属过度投资故未采纳。同时排掉影响 demo 页可用性的间歇 500。
- **验证方式**：
  1. **demo 页浏览器实测**：切换按钮生效，`#h1` 由「全网热点，一屏尽览」↔「Track what's trending across the Chinese internet」，6 项统计标签「活跃话题/跨平台话题/首发溯源/覆盖平台/今日采集/预警总数」↔「ACTIVE TOPICS / CROSS-PLATFORM / ORIGIN TRACES / PLATFORMS / COLLECTION RUNS / ALERTS」；再点一次恢复中文；刷新后语言记忆（localStorage）；话题卡片标题在两版语言下均为中文原文。
  2. **event 页浏览器实测**（`/event?id=1198`）：主标题「事件详情」↔「Event detail」，区块标题「首发溯源」↔「First-source tracing」，表头「平台/标题/热度/名次」↔「Platform/Title/Heat/Rank」；两版语言下事件标题「李嘉诚暂不打算公开露面回应传闻」保持中文，平台标签（toutiao）、热度（237,435）、榜单成员列表均正常渲染；控制台无 JS 报错。
  3. **并发回归（修复验证）**：12 线程 × 5 端点 × 3 轮 = 180 次请求，结果 **200 × 180，零 500**（修复前服务日志存在间歇 500 与 `OperationalError: view ... already exists`）。
  4. 模板为热加载（`web.py` 每请求读盘），前端改动即时生效；`web.py` 改动已重启服务加载。

### #41 · 2026-10-16 · demo 页移除管理后台入口（公私彻底分离）

- **变更点**：
  1. **`app/templates/demo.html`**：删除话题区右上角「进入管理看板 → / Admin dashboard →」链接（`href="/"`），同步清理残留：i18n 字典中 `secMore` 双语键、`renderStatic()` 中对已删除节点的 `textContent` 赋值（不删会抛 TypeError）、CSS `.sec-more` 两条样式。
- **涉及文件**：`app/templates/demo.html`
- **原因**：demo 是公开门面，`/` 管理看板需鉴权，公开访客点进去只会看到鉴权弹窗与空壳页，体验差且暴露后台存在；公开部署前需做到 demo 无任何指向管理入口的链接。
- **验证方式**：全局搜索 `sec-more|secMore` 零命中；`href="/"` 在 demo/event 模板中零命中；web 每次请求实时读模板，刷新页面即生效，无需重启进程。

### #40 · 2026-10-16 · 新增英文版 README（README.en.md）+ 中英互链

- **变更点**：
  1. 新增 `README.en.md`：中文 README 的完整英文对照版，覆盖核心能力、界面预览（复用同 3 张截图）、快速开始、常用命令、项目结构、环境变量、Docker 部署、数据源配置、MCP 接入、License 全部章节；`sources.toml` 示例保持原文（含中文 `name` 字段原样保留，因示例即真实配置）。
  2. `README.md` 顶部新增语言切换行 `[English](README.en.md) | 中文`；`README.en.md` 顶部为 `English | [中文](README.md)`。
  3. 两版 README 的项目结构块补充 `README.en.md` 条目。
- **涉及文件**：`README.en.md`（新增）、`README.md`、`CHANGELOG.md`
- **原因**：面向 GitHub 国际受众；英文 README 是开源项目 star 转化的常规配置，配合已更名的 FirstDrop 品牌（传播性更强的英文友好名）。
- **验证方式**：
  1. 截图路径 `docs/images/*.png` 为仓库相对路径，`README.en.md` 位于仓库根目录，与 `README.md` 同级，图片链接同样可解析。
  2. 双向语言链接互指正确（`README.md` ↔ `README.en.md`）。
  3. 英文版章节与中文版一一对应，命令表/环境变量表/Docker 说明内容等价。

### #39 · 2026-10-16 · README 补充真实界面截图（3 张）

- **变更点**：
  1. 新增 `docs/images/` 目录，收录 3 张真实运行截图（无头浏览器渲染实际页面，非示意图）：
     - `demo-home.png` — Demo 落地页（品牌区 / 6 项统计 / 跨平台话题卡片 / 首发溯源时间线 / 页脚）
     - `event-detail.png` — 事件详情页（覆盖平台 / 峰值热度 / 首发溯源区块 / 各平台榜单表现）
     - `daily-brief.png` — 每日舆情日报（总览数字 + 高优先级预警 + 事件摘要卡片）
  2. `README.md` 在「核心能力」与「快速开始」之间新增「## 界面预览」小节，逐图配一句说明。
- **涉及文件**：`README.md`、`docs/images/demo-home.png`、`docs/images/event-detail.png`、`docs/images/daily-brief.png`
- **原因**：GitHub star 转化最大的短板是缺视觉素材；真实截图能直观传达产品形态（跨平台议题聚合 + 首发溯源 + 自动日报）。
- **验证方式**：
  1. 用无头浏览器（Chrome `--headless=new` / Edge）以 1440 宽渲染 `http://localhost:8000/demo`、`/event?id=1198`、`/data/briefs/20261016.html` 并截屏，产物落在 `docs/images/`。
  2. 逐张目视复核：demo 页含完整话题卡片与溯源时间线、事件页含溯源区块与榜单表、日报页含总览与事件卡片，均为真实数据渲染，无「加载失败」空态。
  3. 截图路径为仓库相对路径 `docs/images/*.png`，`docs/` 不在 `.gitignore` 排除范围内，随仓库一并提交。

### #38 · 2026-10-16 · 项目更名 Sourcely → FirstDrop

- **变更点**：
  1. **品牌名替换**（`Sourcely` → `FirstDrop`）：README 标题、demo.html 页头 logo 区（图标 `S`→`F`、品牌文字）、demo/event 页面 `<title>`、brief.py 日报标题与页脚及 LLM 系统提示词与 UA、mcp_server.py `SERVER_INFO`（`sourcely-mcp`→`firstdrop-mcp`）、docker-compose.yml 注释、LICENSE 版权署名、docs/mcp-setup.md 全文。
  2. **目录重命名**：`soureci-mvp` → `firstdrop`（含 README 目录结构、runner.py 用法注释、mcp-setup.md 的 `cd` 命令与 `cwd` 配置路径）。
  3. **MCP 服务标识**：配置 JSON 中的 `mcpServers` 键 `sourcely` → `firstdrop`。
  4. **管理看板 token 存储键**：`localStorage` 键 `sourcely_admin_token` → `firstdrop_admin_token`（无存量用户，直接切换）。
  5. **CHANGELOG 头部**项目名更新为 FirstDrop。
- **涉及文件**：`README.md`、`LICENSE`、`docker-compose.yml`、`CHANGELOG.md`、`docs/mcp-setup.md`、`app/brief.py`、`app/mcp_server.py`、`app/runner.py`、`app/templates/demo.html`、`app/templates/event.html`、`app/templates/index.html`；目录 `soureci-mvp` → `firstdrop`
- **原因**：根据 GitHub star 潜力评审，`FirstDrop` 品牌传播性更强（"drop" 为互联网原生词，契合爆点预警产品调性），且无高 star 同名项目与商标冲突；配套叙事（New Drop / Daily Drops / `firstdrop` MCP）天然自成体系。
- **验证方式**：
  1. 全局搜索 `sourcely|soureci-mvp`：源码/文档/配置中已达零残留；仅 `data/briefs/20261015.{md,html}` 保留旧品牌（历史生成物，`data/` 已被 .gitignore 排除，不入库）。
  2. 停进程 → `Rename-Item` 目录为 `firstdrop` → 从新路径重启 web 与 scheduler；web 启动日志确认数据库路径已指向 `firstdrop\data\hotspot.db`。
  3. 端点回归：`/`、`/demo`、`/api/summary`、`/api/clusters` 均 200。
  4. 简报生成验证：`python -m app.brief` 产出 `20261016.{html,md}`，正文品牌为 `FirstDrop 舆情日报`（10 事件、规则模板兜底）。
  5. 保留：CHANGELOG #1 中 `soureci.com` 字样为竞品域名事实记录，未改动。

### #37 · 2026-10-15 · 鉴权信任模型重构（GET 侧统一 + 反代旁路防护）

- **变更点**：
  1. **`app/web.py`**：`do_POST` 与 `do_GET` 鉴权统一改为：配置了 `ADMIN_TOKEN` 时一律强制校验 `Authorization: Bearer <token>`，不再对 loopback 例外放行（反代/隧道场景下 loopback 不可信）；未配置 token 时，将带 `CF-Connecting-IP` / `X-Forwarded-For` 代理头的请求视为外部流量（即使源地址是 127.0.0.1 也拒绝）。新增 `_check_admin_auth()` 统一校验逻辑、`_is_local()` 代理头感知判断、`PUBLIC_GET_PATHS` 公开只读 API 白名单（`/api/summary`、`/api/clusters`、`/api/event`、`/api/trace`、`/api/briefs`、`/api/snapshot`）。
  2. **`app/templates/index.html`**：管理看板 JS 全局引入 `TOKEN`（从 `localStorage` 读取），`api()` 与 `post()` 统一在请求头附加 `Authorization: Bearer <token>`；受保护接口返回 401/403 时自动 `prompt` 请求输入 token 并保存到 localStorage，支持一次输入后后续请求自动携带。
  3. **`README.md`**：`ADMIN_TOKEN` 环境变量说明更新，点明"配置后所有管理类 GET/POST 强制校验"及代理头防护行为。
- **涉及文件**：`app/web.py`、`app/templates/index.html`、`README.md`
- **原因**：第三方评审指出同机反代（cloudflared/nginx/caddy）转发后源地址仍是 127.0.0.1，原 `_is_loopback()` 例外会让公网流量直接绕过鉴权；同时 GET 侧管理接口（`/api/config`、`/api/suggest` 等）对公网暴露配置与别名数据。
- **验证方式**：
  1. **无 token 模式**：本机直接访问管理 GET/POST → 200/400（放行）；带 `CF-Connecting-IP`/`X-Forwarded-For` 代理头 → 管理 GET 403 / POST 403（反代旁路被阻断）；公开只读 API（summary/clusters/demo）带代理头 → 200（不受影响）。
  2. **有 token 模式**（`ADMIN_TOKEN=testtoken123`）：loopback POST 无头 → 401（旧逻辑放行已被关闭）；loopback POST 错 token → 401；loopback POST 对 token → 进入业务层（trace/run 真实执行耗时，timeout 证实通过鉴权）；loopback GET `/api/config` 无头 → 401、对 token → 200；外部代理头+token → 200、仅代理头 → 401。公开只读 API 无头 → 200。
  3. 浏览器回归：`/` 管理看板统计数字与别名候选/数据源配置标签页正常渲染；`/demo` 统计卡片与话题卡片正常渲染；web 进程已重启加载新代码。

### #36 · 2026-10-15 · 发布安全与工程交付修复（鉴权 / Docker / LICENSE / 代码清理）

- **变更点**：
  1. **`app/web.py`**（P0 鉴权）：新增 `ADMIN_TOKEN` 环境变量支持；`do_POST` 增加来源校验——本机（loopback）始终放行，非本机请求在未配置 token 时一律 403、配置后需携带 `Authorization: Bearer <ADMIN_TOKEN>`（否则 401）；`main()` 默认监听地址改为动态：未配置 token 时 `127.0.0.1`，配置后 `0.0.0.0`（`--host` 可覆盖）。
  2. **`Dockerfile`**（P1）：移除 `COPY data/ ./data/`（不再把运行中的 hotspot.db/-wal/-shm 打进镜像），新增 `requirements.txt` 复制与 `PYTHONIOENCODING/PYTHONUNBUFFERED` 环境变量，`CMD` 显式 `--host 0.0.0.0`（容器内无 token 时远程写接口 403，默认只读安全）。
  3. **`docker-compose.yml`**（新增，P1）：web + scheduler 双服务，共享 `./data:/app/data` volume，透传 `ADMIN_TOKEN` 与 `LLM_BRIEF_*` 环境变量；scheduler 执行 `--schedule --brief`（采集 + 每日 23:30 简报）。
  4. **`.dockerignore`**（新增，P1）：排除 `data/`、`__pycache__/`、`.git/`、`docs/`、`.env` 等，防止数据与敏感文件进镜像。
  5. **`LICENSE`**（新增，P1）：MIT 正文（README 此前仅写 "MIT" 无正文）；**`.gitignore`**（新增，P1）：忽略 `data/`、`*.db*`、`__pycache__/`、`.env`、IDE 目录，防止数据库/快照/简报被 push。
  6. **`app/web.py`**（P2 清理）：删除 do_POST 之后重复的 `api_event` 定义（与 L437 版完全相同的死代码）；`api_event` 的 `id` 参数解析包 try/except，非数字返回 `{"error": "id 参数不合法"}`（before：ValueError → 500；after：400）；`do_GET` 对含 `error` 键的 dict 统一返回 400（与 do_POST 口径一致，如「事件不存在」before 200 / after 400）。
  7. **`README.md`**：环境变量表新增 `ADMIN_TOKEN` 行；新增「Docker 部署」节（双服务说明、首次需等采集周期、远程管理用法、数据不打包进镜像说明）；License 段落链接到 LICENSE 文件；目录结构补 `docker-compose.yml`、`LICENSE`。
- **涉及文件**：`app/web.py`、`Dockerfile`（重写）、`docker-compose.yml`（新增）、`.dockerignore`（新增）、`LICENSE`（新增）、`.gitignore`（新增）、`README.md`
- **原因**：按第三方评审意见补齐开源发布前的非功能项——公网部署 blocker（管理接口零鉴权）、Docker 实际不可用（数据打进镜像 + 无采集进程）、缺 LICENSE/.gitignore、代码整洁（重复定义与 500 口径）。
- **验证方式**：
  1. 鉴权矩阵（经 LAN IP 192.168.42.247 访问 `--host 0.0.0.0` 测试实例）：未配置 token → GET 200 / POST 403；配置 `ADMIN_TOKEN=testtoken123` → POST 无头 401 / 错 token 401 / 对 token 进业务层 400（参数不合法）/ GET 200（读接口不拦）。
  2. loopback 回归：`127.0.0.1:8000` 无 token POST `/api/trace/status` 放行进业务层（400=空 body 参数错误）；`main()` 未配置 token 时默认监听 `127.0.0.1`（启动日志确认）。
  3. 端点回归：`/`、`/demo`、`/event?id=1564`、`/api/summary`、`/api/event?id=1564`（200，members/timeline 正常）、`/api/briefs`、`/data/briefs/20261015.html` 均 200；`/api/event?id=abc` 400、`/api/event?id=1308`（簇已不存在）400、路径穿越 404。
  4. 进程：web 已重启加载新代码；scheduler（`--schedule --brief`）本轮无代码改动，未重启。

### #35 · 2026-10-15 · P0-P2 端到端验证修复（4 处 bug）

- **变更点**：
  1. **`app/web.py`**：`api_event` / `api_briefs` 函数定义移至 `ROUTES` dict 之前，修复启动即 `NameError: name 'api_briefs' is not defined`。
  2. **`app/templates/event.html`**：`load()` 中请求路径由 `/event?id=` 改为 `/api/event?id=`（原路径返回 HTML 模板自身，导致 `.json()` 解析报 `Unexpected token '<'`，整页渲染失败）。
  3. **`app/templates/demo.html`**：修复 `loadTopics()` 中模板字符串在 L233 提前闭合（行尾多一个反引号）导致的 `SyntaxError: Unexpected token 'class'`，该错误使整个 script 块存在失效风险。
  4. **`app/templates/demo.html`**：`loadBrief()` 由 `fetch(HEAD /data/briefs/YYYYMMDD.html)` 改为 `GET /api/briefs` 匹配当天文件名（服务未实现 `do_HEAD`，HEAD 请求返回 501 产生控制台噪音）；今日无简报时隐藏入口按钮。
- **涉及文件**：`app/web.py`、`app/templates/event.html`、`app/templates/demo.html`
- **原因**：#34 代码完成后端到端验证发现的实测缺陷，全部修复后六端点验证通过。
- **验证方式**：
  1. `python -c "import app.web"` 导入成功，ROUTES 含 `/api/briefs`、`/api/event`（before：NameError 启动失败 → after：14 条路由注册成功）。
  2. 六端点实测：`/demo` 200、`/event?id=1308` 200、`/api/event?id=1308` 200（返回 cluster/earliest/members=7/timeline=187）、`/api/briefs` 200、`/data/briefs/20261015.html` 200；路径穿越 `/data/briefs/../../config/sources.toml` 及 URL 编码变体均 404。
  3. `brief.fetch_events()` 返回 10 个事件，逐一校验均为今日活跃（stale 列表为空），今日过滤生效。
  4. 浏览器复验：`/demo` 统计卡片/话题卡片/溯源时间线全部渲染，控制台无 SyntaxError，日报按钮 href 正确指向当日简报；话题卡片点击新标签页打开 `/event?id=1308`；`/event` 详情页簇信息、成员列表、溯源区块渲染正常。
  5. 备注：压测期间 `/api/summary` 出现 1 次瞬态 500（与 scheduler 采集写入并发竞争），前后请求均 200，服务自愈，未做额外改动。
  6. 进程状态：web（8000 端口）与 scheduler（`--schedule --brief`，每日 23:30 简报）均已重启加载最新代码。

### #34 · 2026-10-15 · P0 补闭环 + P1 事件详情页 + P2 发布文档三件套

- **变更点**：
  1. **`app/scheduler.py`**：新增 `daily_fn` / `daily_hour` / `daily_minute` 参数与 `_check_daily()` 方法，支持每日定时任务（同日仅触发一次）；`tick()` 在每次循环时检查是否到达每日触发时刻。
  2. **`app/runner.py`**：`--schedule` 模式新增 `--brief` 参数，启用后每日 23:30 自动执行 `brief.generate()`；导入 `brief` 模块。
  3. **`app/brief.py`**：`fetch_events` 补今日过滤（`WHERE EXISTS topic_cluster.updated_at` 在指定日期范围内），与 stats 口径对齐；`generate()` 支持 `date` 参数生成历史日期简报；`_render_html`/`_render_md` 的 LLM 结果回链改为按 `cluster_id` 匹配（`llm_map`），避免数组下标错位。
  4. **`app/web.py`**：`/data/briefs/` 静态路由补路径穿越防护（`..` 和 `\` 过滤）；新增 `GET /api/briefs` 简报历史列表；新增 `GET /api/event` 事件详情 API；新增 `GET /event` 事件详情只读页路由。
  5. **`app/templates/event.html`**（新增）：事件详情只读页——簇信息（平台覆盖/热度进度条/话题条目）、首发溯源卡片（含来源链接、置信度、快照入口）、各平台榜单成员列表、热度时间线。
  6. **`app/templates/demo.html`**：话题卡片点击由跳管理看板改为跳 `/event?id=cluster_id`（公开只读详情）；热度进度条 `maxHeat` 改为动态取当前列表最大值，消除硬编码 10000 导致的满条问题。
  7. **`README.md`**（新增）：项目介绍、核心能力、快速开始、常用命令、项目结构、环境变量、数据源配置示例。
  8. **`requirements.txt`**（新增）：注明纯标准库零依赖，Python >= 3.12。
  9. **`Dockerfile`**（新增）：基于 python:3.12-slim，默认启动看板服务（8000 端口），数据目录已挂载。
  10. **`docs/mcp-setup.md`**（新增）：MCP Server 接入指南（Claude Desktop / Cursor 配置 JSON 示例、使用示例、注意事项）。
- **涉及文件**：`app/scheduler.py`、`app/runner.py`、`app/brief.py`、`app/web.py`、`app/templates/event.html`（新增）、`app/templates/demo.html`、`README.md`（新增）、`requirements.txt`（新增）、`Dockerfile`（新增）、`docs/mcp-setup.md`（新增）
- **原因**：按评审意见完成 P0（每日简报定时、今日过滤、路径穿越防护、LLM 回链）、P1（事件详情只读页）、P2（README/requirements/Docker/MCP 文档），消除三件套「最后一公里」缺口。
- **验证方式**：
  1. `scheduler.tick()` 在 23:30 后触发 `brief.generate()`，且同日不重复。
  2. `brief.generate(date="2026-10-15")` 仅取当日活跃事件，stats 与 events 口径一致。
  3. `GET /data/briefs/../../etc/passwd.html` 返回 404（路径穿越被拦截）。
  4. `GET /api/briefs` 返回简报列表。
  5. `GET /event?id=xxx` 返回事件详情只读页，demo 卡片点击跳转正确。

### #33 · 2026-10-15 · Demo站 + LLM简报 + MCP Server 三件套落地

- **变更点**：
  1. **`app/templates/demo.html`**（新增）：公开 Demo 落地页，设计精美、只读无操作。顶部品牌区+核心数据总览（活跃话题/跨平台话题/首发溯源/覆盖平台/今日采集/预警总数）；中间「跨平台热议话题」卡片网格（含平台覆盖标签+热度进度条）；「事件首发溯源」时间线（含首发出处链接、置信度、正文摘要）；自动探测并展示「今日舆情日报」入口。
  2. **`app/web.py`**：新增 `/demo`、`/demo.html` 路由（从磁盘读模板，改模板无需重启）；`api_summary` 新增 `with_signal` 统计（首发溯源记录数）；新增 `/data/briefs/` 静态文件服务（支持 .html/.md）。
  3. **`app/brief.py`**（新增）：舆情日报生成器。从 `v_cluster` + `v_event_track` 取当日 Top 事件（含各平台榜单成员），产出 HTML/Markdown 到 `data/briefs/YYYYMMDD.{html,md}`。LLM 接入可选（环境变量 `LLM_BRIEF_PROVIDER`/`LLM_BRIEF_API_KEY`/`LLM_BRIEF_MODEL`），未配置时用规则模板兜底，不虚构信息。
  4. **`app/mcp_server.py`**（新增）：MCP Server（stdio JSON-RPC，零第三方依赖）。暴露四个工具：`get_trending`（当前全网热点）、`get_event_origin`（某事件最早出处）、`search_events`（关键词查事件簇）、`get_daily_brief`（取最新日报）。
- **涉及文件**：`app/templates/demo.html`（新增）、`app/web.py`、`app/brief.py`（新增）、`app/mcp_server.py`（新增）、`data/briefs/`（运行时生成）
- **原因**：按《GitHub star 潜力分析》建议，落地「公开 Demo 站 + LLM 每日简报 + MCP Server」三件事，分别解决「看到价值、转发素材、蹭 AI 流量」。
- **验证方式**：
  1. `GET /demo` 返回 200，页面包含品牌区、数据总览、话题卡片、首发溯源时间线。
  2. `python -c "from app import brief; brief.generate()"` 成功生成 `data/briefs/20261015.html`（14.9KB）和 `.md`，`llm_used=false`（规则模板兜底）。
  3. `GET /data/briefs/20261015.html` 返回 200。
  4. MCP Server 四个工具（get_trending / get_event_origin / search_events / get_daily_brief）全部通过内部调用测试。

### #32 · 2026-10-15 · 每日溯源预算改为可配置参数（接入「数据源配置」页）

- **变更点**：
  1. **`app/config.py`**：`DEFAULTS` 新增 `trace_daily_quota = 60`；`Config` 新增字段 `trace_daily_quota`（默认 60）；`load()` 从 `[defaults]` 读取（缺省回落 60，**旧配置文件无需改动即兼容**）；`build()` 新增 `trace_daily_quota` 参数并按 `_int(..., minimum=1)` 校验；`dumps()` 写入 `[defaults].trace_daily_quota`。
  2. **`app/trace.py`**：新增 `daily_quota()`——从 `config/sources.toml` 读取预算，配置缺失/损坏时回落常量 `DAILY_SEARCH_QUOTA`；`run()` 签名由 `quota: int = DAILY_SEARCH_QUOTA` 改为 `quota: int | None = None`，缺省时调用 `daily_quota()`（`DAILY_SEARCH_QUOTA` 退化为兜底默认值）。
  3. **`app/web.py`**：`api_config` 返回 `trace_daily_quota`；`api_save_config` 透传 `body.get("trace_daily_quota")` 给 `build()`；`api_trace` 的 `counts["quota"]` 由常量改为 `trace_mod.daily_quota()`。
  4. **`app/templates/index.html`**：配置页 `cfgbar` 新增「每日溯源预算(个)」数字输入（`#trace-quota`，min=1），`renderConfig` 从接口回填、`saveConfig` 随 `POST /api/config` 提交，新增/删除数据源重绘时同步保留该输入值。
- **涉及文件**：`app/config.py` / `app/trace.py` / `app/web.py` / `app/templates/index.html`
- **原因**：用户要求「将预算量作为一个可配置参数放到配置菜单」。此前 `DAILY_SEARCH_QUOTA = 60` 硬编码在 `app/trace.py`，调整预算需改代码并重启进程；配置化后可直接在看板「数据源配置」页修改并保存到配置文件。
- **验证方式**：
  1. 单元校验（Python 3.12 直调 `app/config.py` + `app/trace.py`）：`load()` 旧配置回读 `trace_daily_quota = 60`；`trace.daily_quota() = 60`；`build(quota=250) → 250`、未传 → 60；`dumps()` 产物含 `[defaults].trace_daily_quota` 且可被 `load()` 回读（roundtrip = 250）；非法值 `"abc"` / `0` / `-5` 分别被拒（「必须是整数」/「不能小于 1」）。
  2. 接口实测（重启后 `http://127.0.0.1:8000`）：`GET /api/config` → `trace_daily_quota = 60`；`GET /api/trace` → `counts.quota = 60`；`POST /api/config {..., trace_daily_quota: 80}` → 返回 `config.trace_daily_quota = 80` 且配置文件出现 `trace_daily_quota   = 80`；非法值 `0` → HTTP 400 拒绝。
  3. 接口测试后已将 `config/sources.toml` 还原为原始文件（保留手写注释），复读 `GET /api/config` 回落到 60。
  4. 已重启 web + scheduler（`app/trace.py` 的 `run()` 默认值变更需重启才在定时溯源中生效）；顺带清理了重复运行的 web/scheduler 进程（各保留 1 个）。
- **备注**：预算值默认仍为 60，未做实际调整；如需一次清空积压，可在配置页把「每日溯源预算」改大后保存（保存后**无需**改代码，但调度器需重启以加载新默认值——因 `run()` 是模块级代码）。

---

### #31 · 2026-10-15 · 「执行溯源」改为连续分批跑完待溯源（修复只跑 3 个 + 长事务锁库）

- **变更点**：
  1. **`app/templates/index.html`**：前端写死的 `post('/api/trace/run', {limit:3})` 移除，改为新增 `runTraceAll()`——点击「执行溯源」后**分批循环**（每批 `CHUNK=5`），直到待溯源清空或当日预算（`DAILY_SEARCH_QUOTA`）用尽；按钮右侧灰字区实时显示进度「溯源中… 已处理 X/Y 个（每批 5 个）」，结束时给出小结（预算用尽时提示「今日预算已用尽（N/60）· 剩余 M 个待溯源」）。
  2. **`app/trace.py` `run()`**：`conn.commit()` 从「整批结束后提交一次」改为**每事件提交**。原因：溯源单个事件要联网检索数十秒，事务跨事件持有会长时间锁住 SQLite（先写后不提交 → 写锁持续到批量结束），导致调度器的 `fuse.py` 报 `sqlite3.OperationalError: database is locked` 并整进程崩溃。
- **涉及文件**：`app/templates/index.html` / `app/trace.py`
- **原因**：用户反馈「点击执行溯源之后，实际只有 3 个被执行了」——根因即前端 `{limit:3}` 写死（此前 #28 只修了按钮**显示**的数字，没修**实际执行**批量）；实证批量执行后调度器崩溃（长事务锁库），一并修复。
- **验证方式**：
  1. 浏览器实测（`http://localhost:8000/?v=20261015a`，强刷确保非缓存）：`runTraceAll` 已加载、页面内 `{limit:3}` 返回 -1；点一次按钮后 100 秒内处理 **15 个**事件（每批 5），进度文案「溯源中… 已处理 15/208 个（每批 5 个，联网检索较慢）」，待溯源 208 → 193、已溯源 17 → 32，console 无报错。
  2. 并发回归：`POST /api/trace/run {limit:5}` 运行期间调度器完成完整一轮（采集 7 平台 → 加工 312 行 → 对齐 270 簇 → 融合 270 实体 → 预警新增 22 → 溯源登记），日志 `database is locked` 计数 **0**（修复前该场景调度器直接崩溃退出）。
  3. 已重启 web + scheduler；单批 5 个返回 `traced=5 / signals=3`，`today_used` 37 → 42。
- **备注**：当日预算 60 条/天（`app/trace.py: DAILY_SEARCH_QUOTA`）为设计护栏，待溯源积压（约 190 条）需跨天消化；如需一次跑完需上调该常量。

---

### #30 · 2026-10-15 · 快照查看方式改为弹窗内嵌预览（修复 HTTPS 强制升级导致的 ERR_SSL_PROTOCOL_ERROR）

- **变更点**：
  1. **`app/templates/index.html`**：「查看快照」由「新标签打开 `<a href="/api/snapshot?...">`」改为**弹窗内嵌 iframe 预览**（新增 `openSnapshot(path)`，按钮二次点击可收起），iframe 用同源相对路径 `/api/snapshot?path=...` 且加 `sandbox=""`（禁用第三方存档页脚本，避免其在本站源下执行）。
  2. iframe 下方显示本地存档文件路径（`data/snapshots/xxx.html`），作为存档直读的兜底入口。
  3. 清理 `data/app.db`（0 字节空文件，为诊断脚本误建的残留，非服务使用；服务 DB 始终是 `data/hotspot.db`）。
- **涉及文件**：`app/templates/index.html`
- **原因**：用户反馈「德国国债开盘走高…」条目的快照点击后弹出浏览器错误页。排查确认：**快照文件与接口本身正常**（`/api/snapshot?path=snapshots/1071_20261015_160418.html` 返回 200 / 97,796 字节 / 含「德国」「国债」「收益率」，文件为新浪财经原文，97 KB）；真正的报错是浏览器把新标签跳转的 `http://localhost:8000` 强制升级为 `https://localhost:8000`，而看板是纯 HTTP 服务，TLS 握手失败 → `ERR_SSL_PROTOCOL_ERROR`。改为同源内嵌 iframe 后不再发生跨标签的协议升级。
- **验证方式**：
  1. 服务端自检：`GET /` 返回的页面含 `function openSnapshot` / `id="snapbox"` / `查看快照</button>`，旧 `<a>` 链接已消失；内联脚本 `node --check` 语法通过。
  2. 浏览器实测（http://localhost:8000）：进入「首发溯源」→ 筛选「已溯源」→ 点「德国国债」行 → 点「查看快照」，弹窗内 iframe 成功渲染新浪财经原文（可见「德国/国债/收益率」），无 SSL/404/空白错误，console 无 JS 报错；对簇 1157（人民网）同样验证通过。

---

### #29 · 2026-10-15 · 首发溯源：已溯源条目保存 URL 快照到本地

- **变更点**：
  1. **数据库**：`event_earliest_signal` 新增 `snapshot_path TEXT` 字段；`v_event_track` 视图同步暴露。迁移通过 `ALTER TABLE` + `DROP VIEW` + 重建视图完成。
  2. **`app/trace.py`**：新增 `SNAPSHOT_DIR = data/snapshots/` 与 `save_snapshot(url, cluster_id)`；`trace_one` 在确定最早信号后调用 `save_snapshot` 抓原始 HTML 落盘，返回相对路径 `snapshots/{cluster_id}_{YYYYMMDD_HHMMSS}.html` 一并写入 `snapshot_path` 字段。
  3. **`app/web.py`**：`api_trace` SQL 增加 `snapshot_path`；新增 `api_snapshot` 路由（`GET /api/snapshot?path=snapshots/xxx.html`），校验路径前缀防穿越，`do_GET` 对返回 `{file, content_type}` 的响应直接以字节流形式 `_send`。
  4. **`app/templates/index.html`**：首发卡片弹窗在覆盖源下方新增「查看快照」链接（`target="_blank"`），点击在新标签页打开本地保存的 HTML 快照。
- **涉及文件**：`app/schema.sql` / `app/trace.py` / `app/web.py` / `app/templates/index.html`
- **原因**：用户要求「已溯源的条目除了现在的url外还应该保存该url的快照文件到本地」，便于在原文被删除/修改后仍可审计当时内容，同时避免每次点开都要重新请求外部站点。
- **验证方式**：
  1. `save_snapshot` 单测：保存 30,614 字节 HTML，`trace_one` 返回 `snapshot_path` 且 DB 已写入。
  2. 回填：对全部 11 条 active 且 `snapshot_path IS NULL` 的记录重新抓 URL 落盘，11/11 成功。
  3. 端到端：`GET /api/trace?status=active` 返回 11 条均含 `snapshot_path`；`GET /api/snapshot?path=snapshots/1157_20261015_160418.html` 返回 200 / `text/html; charset=utf-8` / 39,353 字节。
  4. 已重启看板进程（`job-529ec9ea6a634075ad16e06da13518e2`）使新路由生效，前端刷新即可看到「查看快照」链接。

---

### #28 · 2026-10-15 · 首发溯源时间提取修复：支持 ISO 8601 `T` 分隔符 + 多层兜底

- **变更点**：
  1. **`_DT` 正则增强**：支持 ISO 8601 `T` 分隔符（`2026-10-15T07:34:38+08:00`）、逗号/中文逗号分隔（`2026年10月15日，13:04`）、可选秒（`HH:MM:SS`）。
  2. **`_iso` 支持 `ss` 参数**，并对 `None` 安全（`hh or 0 / mm or 0 / ss or 0`），修复 `int(None)` 崩溃。
  3. **`extract_article` 时间兜底链重构**：从「meta → JSON-LD → `<time>` → `text[:4000]`」改为「meta/JSON-LD/`<time>` 候选收集 → 优先选带时分的 → `parse_datetime(content)` → `parse_datetime(text)` → `parse_time(content)` → 候选纯日期兜底」。其中 `parse_datetime` 只匹配带时分的时间，避免被 head 里的纯日期误导；`text` 搜整个 HTML（不再截断 `[:8000]`），覆盖「发布时间放在非 `<p>` 标签且位置极靠后」的站点（如人民网 `pos≈19963` 的 `<b id="newstime">`）。
  4. **新增 `parse_datetime(text)`**：只匹配 `_DT`（带时分），不匹配 `_D`（纯日期），用于优先提取精确时间。
  5. **新增 `_is_precise(iso_str)`**：判断 ISO 时间是否精确到时分（非 `00:00:00`），用于 meta/JSON-LD/`<time>` 候选中优先选择精确时间，修复「meta 纯日期抢先 break 导致 JSON-LD 精确时间被跳过」的问题（如大众网 `publishdate=2026-10-14` 覆盖了 JSON-LD `datePublished=2026-10-14 19:24:23`）。
  6. **纯日期候选兜底顺序修复**：将 `if not posted and cands: posted = cands[0]` 从「候选收集后立即执行」移到「所有 content/text 兜底都失败后才执行」，避免纯日期抢先阻止后续更精确时间的搜索（如人民网 meta `publishdate=2026-10-15` 导致 `<b id="newstime">2026年10月15日08:23</b>` 被跳过）。
  7. **空标题事件过滤**：`register_triggers` 跳过 `label` 为空的簇（`v_cluster.label` 来自 `MAX(li.title)`，当 `topic_cluster` 成员在 `v_latest_item` 中无匹配时为 NULL）；同时清理已有空标题事件（430/769/803）标记为 `ended`。
- **涉及文件**：`app/trace.py`、`CHANGELOG.md`。
- **原因**：用户抽检发现已溯源条目中存在「未产出」「时间未知（T00:00:00）」、DeepSeek 恢复服务时间不准确、以及待溯源列表中出现空标题条目的问题。根因：① `_DT` 不支持 `T` 分隔符导致 sina/bjnews 等站点的 meta 时间只解析到日期；② `text[:4000]` / `text[:8000]` 兜底范围不足，正文时间常在更后面（如人民网 pos≈19963）；③ mydrivers 等站把发布时间放在非 `<p>` 标签里，正文聚合后丢失；④ `_iso` 收到可选秒组 `None` 时 `int(None)` 崩溃；⑤ meta 纯日期抢先 `break` 导致后续精确时间被跳过；⑥ 纯日期候选过早采用，阻止了 content/text 兜底搜索；⑦ `topic_cluster` 成员在 `v_latest_item` 中无匹配时 `v_cluster.label` 为 NULL，导致注册空标题事件。
- **验证**：
  - **bjnews（DeepSeek恢复服务）**：`2026-10-15T00:00:00` → **`2026-10-15T13:04:00`**（正文 `2026年10月15日，13:04`）。
  - **sina（苹果市值）**：`2026-10-15T00:00:00` → **`2026-10-15T07:34:38`**（meta `article:published_time`）。
  - **sina（德国国债）**：`2026-10-08T00:00:00` → **`2026-10-08T00:43:18`**。
  - **mydrivers（Gemini4）**：`未产出` → **`2026-10-14T12:14:36`**。
  - **1069（A股算力）**：`未产出` → **`2026-10-15T14:27:07`**（第一财经）。
  - **304（中国电建）**：搜狗微信跳转链接（无时间）→ 网易直接链接 **`2026-10-15T10:12:15`**。
  - **536（「耻辱」合影事件）**：`2026-10-14T00:00:00` → **`2026-10-14T18:41:38`**（新浪财经，重跑后选到更早的精确时间；大众网原始页面 JSON-LD 实际为 `19:24:23`）。
  - **1157（10封书信读懂用命换命的信任）**：`2026-10-15T00:00:00` → **`2026-10-15T08:23:00`**（人民网贵州频道 `<b id="newstime">2026年10月15日08:23</b>`，修复 `text[:8000]` 截断导致搜不到的问题）。
  - 批量重跑 **11 个 active 事件**：**全部产出精确时间**，无「未产出」、无「时间未知」、无 `T00:00:00`。
- **运维提示**：修改了 `app/trace.py`，已重启看板（8000）与 `--schedule` 采集进程。

---

### #27 · 2026-10-15 · 新增「首发溯源」子系统（触发式注册表 + 多源搜索广撒网 + 通用正文抽取 + 最早信号）

- **变更点**：
  1. **`schema.sql` 新增两张表 + 一个视图**：
     - `event_track`（事件跟踪注册表）：`cluster_id` 主键，`tier`(L1/L2)、`status`(pending/active/paused/ended)、`trigger_reason`、`platform_count`、`platforms`、`peak_hot`、`best_position`、`coverage`、`trace_count`、`triggered_at`、`last_traced_at`、`updated_at`。索引 `idx_event_status` / `idx_event_tier`。
     - `event_earliest_signal`（首发信号）：`cluster_id` 主键，`url`、`site`、`posted_at`、`title`、`content_excerpt`、`source_coverage`、`confidence`、`is_manual`、`searched_at`。
     - 视图 `v_event_track`：`event_track LEFT JOIN event_earliest_signal`，每次 `DROP VIEW IF EXISTS` 重建，供看板单口查询。
     - 迁移方式：`CREATE TABLE IF NOT EXISTS` + 视图重建，旧库启动即自愈（无需 `db.py` 迁移逻辑）。
  2. **新建 `app/trace.py`（首发溯源全部逻辑）**：
     - **触发判定 `register_triggers`**：L1 = 跨平台共振(`platform_count>=2`) / 榜首(`best_position<=3`) / 名次跃升(曾产生 cluster 预警)；L2 = TOP10 持续(`best_position<=10` 且 `seen_runs>=3`)。同一 `cluster_id` 只注册一次；单轮新增上限 `MAX_NEW_TRIGGERS=40`，按 L1 优先、`platform_count`/`peak_hot` 降序截断。
     - **可插拔多源搜索 `SOURCES` + `search_news`**：实装 3 个可达源——**搜狗微信**(`sogou_weixin`，公众号)、**360新闻**(`so_news`，`news.so.com/ns`，含 `data-url` 真实链接 + summary 内绝对时间)、**360网页**(`so_web`，`www.so.com/s`，`data-mhurl`/`mh-news-title` 新闻卡片)。设计稿建议的百度新闻(`s?tn=news` 返回安全验证页)、必应(`format=RSS` 被忽略)、Google(超时)、微博/知乎/B站搜索 API(403/412/验证页) 实测不可用，未纳入；注册表可后续加源。返回 `(候选, 命中源清单)`，命中源写入 `source_coverage`。
     - **通用正文抽取 `extract_article`**：基于 `html.parser.HTMLParser` 子类，抽取 meta(`og:title`/`article:published_time`/`datePublished` 等) + `<time datetime>` + JSON-LD `datePublished` 正则 + `<p>` 段落聚合(len≥15)，纯标准库实现。
     - **相关度 `_relevance`（bigram 覆盖率，抗长文稀释、对词序不敏感）**：`|Q_bigram ∩ T_bigram| / |Q_bigram|`，并用 `_STOP` 通用字集过滤虚词 bigram；阈值 `RELEVANCE_MIN=0.35`。
     - **时间窗口护栏 `LOOKBACK_DAYS=90`**：早于触发前 90 天的候选直接判为「同话题不同事件」剔除——压制误报最关键护栏。
     - **置信度 `_confidence`**：`0.35*coverage_score + 0.25*time_score + 0.25*relevance + 0.15*authority`，2 位小数；`AUTHORITATIVE` 为官方/权威站点白名单。
     - **执行器 `trace_one` / `run`**：`trace_one` 取代表标题(+≤1 条不同措辞成员标题)为查询，候选按 bigram 相关度过滤→时间窗剔除→按 `posted_at` 升序，取 `FETCH_TOP_K=5` 逐条正文抽取，先命中时间最早者为首发信号，`INSERT OR REPLACE` 写入 `event_earliest_signal`；`run` 按每日预算 `DAILY_SEARCH_QUOTA=60` 批量处理 `pending` 事件。
     - **人工优先 `set_manual_signal`**：人工补链写 `is_manual=1`，自动溯源检测到 `is_manual=1` 时**跳过、不覆盖**。
  3. **`runner.py`**：`enrich()` 末尾挂入 `trace.register_triggers`（只登记，不联网，避免阻塞采集循环）；新增 CLI `--trace` / `--trace-limit`（默认 5）。
  4. **`web.py`** 新增 4 个 API + 路由：`GET /api/trace`（列表 + counts：`with_signal`/`today_used`/`quota` + `sources` 覆盖源清单）；`POST /api/trace/run`（给 `cluster_id` 只溯该事件并置 `active`，否则 `register_triggers` + 批量 `run`）；`POST /api/trace/status`（暂停/恢复/结束）；`POST /api/trace/manual`（人工补链，校验 http(s)）。
  5. **`index.html`** 新增「首发溯源」标签页（位于「历史回溯」之后）：顶部「执行溯源（N 个待溯源）」按钮 + 今日预算/覆盖源说明；chips（全部/待溯源/已溯源/已暂停）；表格列「事件/层级/状态/首发信号/置信度/触发时间/操作」；操作按钮「溯源/补链/暂停-恢复」；点击行弹出**首发卡片**（标题、站点、发布时间、覆盖源、置信度、内容摘要）。页签说明明确「**不声称全网首发**，只保证『已覆盖源中最早』」。
- **涉及文件**：`app/schema.sql`、`app/trace.py`（新增）、`app/runner.py`、`app/web.py`、`app/templates/index.html`、`CHANGELOG.md`。
- **原因**：落地分享设计稿《事件首发溯源与舆情跟踪系统——方案描述》的 **Phase 2**（触发式注册表 + 搜索引擎公开发布广撒网 + 通用正文抽取）。该阶段的「首发溯源」投入产出比最高，故优先生效。
- **范围切分（重要）**：本次**仅实现「首发溯源」**。设计稿中「舆情跟踪」部分（`event_post` / `event_post_metric` / `event_post_sentiment` / `event_timeline` 四表、帖子级采集、情感分析、时间线/曲线）+ 帖子级平台结构化 API 为 **P2–P4 阶段，本次未实现**；`event_track` / `event_earliest_signal` 已按其表结构落地，为后续阶段留好挂载点。
- **验证**：
  - **搜索源实测测绘**：逐一实测百度/必应/Google/搜狗新闻/360(新闻+网页)/微博/知乎/B站/头条，确定仅 3 源可达并如实写入覆盖源清单。
  - **首轮质量修复**：首跑命中 2017/2023/2024 旧文（同话题不同事件，置信度 0.52–0.62），根因是相关度用「全字符集覆盖率 + 阈值 0.30」过松 → 改为 **bigram 覆盖率 + `_STOP` 过滤 + 阈值 0.35**，并叠加 **90 天时间窗**；复测（清空信号退回 pending）后结果转为 2026-10-14/15 近期报道，置信度 0.45–0.77，**3/3 命中且时间合理**。
  - **CLI**：`python -m app.runner --trace --trace-limit 3` 输出 3 条合理信号。
  - **看板接口 E2E**（直连运行中的 8000 服务）：
    - `GET /api/trace` 返回 `counts={'active':3,'pending':81,'with_signal':3,'today_used':3,'quota':60}`、`sources=['sogou_weixin','so_news','so_web']`。
    - 暂停 → `{'ok':True,'status':'paused'}`，行状态 `paused`；恢复 → 行状态 `pending`（**步骤5 PASS**）。
    - 人工补链 → `earliest_url` 生效、`is_manual=1`、站点解析正确；非法 URL `ftp://` → HTTP 400 `{"error":"需要合法的 http(s) 链接"}`（**步骤6 PASS**）。
    - 单事件溯源（cluster 835 苹果市值破 5 万亿）→ `ok=True`，写入首发信号 `finance.sina.com.cn`（发布时间 2026-10-15，置信度 0.73，覆盖源 `['so_news','so_web']`）（**步骤4 PASS**）。
  - **前端渲染**（浏览器验证步骤1–2 PASS）：重载后「首发溯源」按钮出现在「历史回溯」之后；chips「全部/待溯源 81/已溯源 3/已暂停 0」、顶部说明含「今日预算 3/60 · 已产出首发信号 3 条 · 覆盖源 sogou_weixin / so_news / so_web」、表格 7 列齐全、控制台无报错。
  - **测试数据清理**：验证用的人工补链假 URL 与状态改动已从库中回滚（cluster 834 恢复 `pending`）。
- **运维提示**：新增 `web.py` 路由与 `index.html` 前端、`runner.py` 调度挂钩，需**重启看板进程**与 `--schedule` 采集进程（本次均已重启）。
- **已知后续项**：`_STOP`/`_bigrams`/`RELEVANCE_MIN=0.35`/`LOOKBACK_DAYS=90` 为经验常数，需随数据增长复检；`event_track` 的 pending 队列（当前 80+）由每日预算 60 与 `--trace` 逐步消化。

---

### #26 · 2026-10-15 · 新增「历史榜单回溯」标签页（关键词 / 日期 / 平台筛选）

- **变更点**：
  1. **后端 `web.py` 新增 `api_history` + 路由 `/api/history`**：查询 `hot_item` 历史留痕，支持 `q`（关键词模糊匹配 title/norm_title，URL 安全 base64 传参）、`date`（YYYY-MM-DD，匹配 `substr(captured_at,1,10)`）、`platform`（平台代码或 all）、`limit`（默认 200）。返回值含 `rows` + `dates`（最近 30 天有数据的日期，供前端 datalist 提示）+ `limit`。
  2. **前端 `index.html` 新增「历史回溯」标签页**（位于「数据源配置」之后），含：关键词输入框（Enter 触发）、日期选择框（带 datalist 最近日期提示）、平台下拉、查询/重置按钮；结果按 `captured_at DESC, platform_code, position` 排列，每行展示平台/名次/标题（附置顶/盘点拆分/标签）/热度/采集时间；点击任意行可复用现有的 `openTimeline` 弹窗查看热度/名次时间线。
- **涉及文件**：`app/web.py`、`app/templates/index.html`、`CHANGELOG.md`。
- **原因**：用户需要在看板中回溯历史榜单，支持按关键词查询和日期筛选，方便快速定位某一事件的历史上榜记录。
- **验证**：
  - API 干跑：`/api/history?q=`（空）返回 200 条以内，含 `dates` 列表；加 `date=2026-10-15&platform=baidu` 返回只含当日 baidu 的记录。
  - 前端切换「历史回溯」标签，控件渲染正常，输入关键词「郑钦文」按回车筛选正确，点击行弹出时间线复用正常。
- **运维提示**：修改了 `web.py`（看板后端）与 `index.html`（前端），需**重启看板进程**生效。

---

### #25 · 2026-10-15 · 爆点预警去重：同一事件不再同时出现 topic 级 + cluster 级重复预警

- **变更点**：`alerts.py` `detect()` 新增「cluster 优先」去重规则——先扫描 cluster 预警得到本轮应报 `cluster_id` 集合，扫描 topic 时若该话题所属簇已报 cluster 预警，则**跳过 topic 级预警**。修复前同一事件（如「郑钦文退出大阪250赛」）会同时产生 baidu topic 预警和 cluster 850 预警，造成重复展示。
- **涉及文件**：`app/alerts.py`、`CHANGELOG.md`。
- **原因**：原去重 key 按 scope 分开（topic 用 `(platform, title)`，cluster 用 `cluster_id`），同一事件的 topic 和 cluster 预警互不排斥，导致用户看到同一事件出现 2 次。
- **验证**：
  - **干跑对比**：修复前 topic 预警 23 条（含 `toutiao 郑钦文退出WTA250大阪站`），修复后 15 条——8 条属于已报 cluster 的 topic 被跳过，cluster 预警 17 条不变。
  - **实际 detect**：新增 6 条 topic（其他事件）、0 条 cluster，无郑钦文重复。
  - **线上数据**：簇 850（baidu+toutiao）仍正常报 cluster 预警 `medium`；baidu 该话题的 topic 级不再重复出现。
- **运维提示**：修改 `alerts.py`（调度/加工进程代码），已按约定（见 #13）重启 `--schedule` 采集进程。

---

### #24 · 2026-10-15 · 实体提取 + 语义重合判同事件（第三条合并通道）+ 别名候选跨平台影响评估（Q1-B / Q2-B）

- **变更点**：
  1. **`align.py` 抽出纯函数 `compute_clusters(conn, *, threshold, min_shared, extra_aliases=None)`**：把「读快照 → 别名归一 → bigram 倒排 → 并查集合并」的计算逻辑独立出来，**真实对齐（`align`）与反事实评估（`alias_impact`）共用同一函数**，保证两条路径口径逐字一致（`align` 只负责在计算后写 `topic_cluster` 快照 + 复用旧簇 id）。
  2. **新增第三条合并通道「实体约束 + 语义重合」`_entity_rule`**：两标题共享一个**具体实体 span**（`_specific_span` = 最长公共子串中不含通用/功能字、不含数字、长度 ≥3、含 ≥2 个汉字的一段，如「陈小春」「工人长相」「鸭绿江大桥」），且整体**字符集 Jaccard ≥ `_ENT_CHAR_JACCARD`(0.40)** → 判为同一事件。**谓词差异（酷似↔撞脸）在此通道（匹配算法）里消化，不进词典** —— 满足 Q2-B「不允许谓词同义词进词典」。字符集（非 bigram）对词序不敏感，正是为了在谓词被替换时仍能捕捉事件核心实体与关键名词的重合。
  3. **`entity_alias_suggest` 新增 `impact_topics` / `impact_platforms` 两列**（反事实评估：该别名若启用，将并入他簇的话题数 / 覆盖平台数）；`schema.sql` 建表加列，`db.py` `_ensure_column` 迁移补列（旧库自愈）。
  4. **`mine.py` 新增 `_score_impacts`**：每轮挖掘后，用 `align.alias_impact`（基线簇签名只算一次，供全部候选复用）为 `pending`+`approved` 候选打影响分并回写两列；`run()` 返回值新增 `impact_scored` / `impact_active`。
  5. **`web.py` `api_suggest`** 返回 `impact_topics/impact_platforms`，排序改为「待审优先 → 待审内部按影响降序」，使**影响 0 的候选自动落末尾（低优先级分区，但不丢弃，符合 Q1-B）**；`counts` 新增 `pending_active`。**`index.html`** 别名候选页新增「跨平台影响」列（`合并 N 话题 / M 平台` 或 `当前影响 0 · 低优先级`）并更新页签说明。
  6. **`runner.py`** `enrich` 日志补「其中跨平台有效 N 条，余为当前影响 0 的低优先级」。
- **涉及文件**：`app/align.py`、`app/alias.py`（新增 `make_rule`/`build_rules` 供反事实注入）、`app/mine.py`、`app/schema.sql`、`app/db.py`、`app/web.py`、`app/templates/index.html`、`app/runner.py`、`CHANGELOG.md`。
- **原因**：用户拍板 **Q1-B / Q2-B**——(1) 别名候选挖掘规则与实际需求错位：产出的是「单条标题内的括号/简称对」，确认后对「跨平台同一事件聚合」几乎无增量；(2) 需要用**实体提取 + 语义重合分析**替代纯字符串阈值来判定同事件；(3) 不允许谓词同义词进词典。故把「同事件」判定落到匹配算法（实体约束通道），并把候选价值量化为「跨平台影响」，让审核页按影响排序。
- **验证**：
  - **正回归**：`福州一工人长相酷似陈小春`(toutiao, 原独立簇 429) → 并入簇 **571**（簇 571 成员 **5 → 6**：baidu×3 / toutiao×2 / weibo×1）。
  - **负回归（防误并）**：`陈小春直言不会把送走的弟弟找回来`(weibo, 簇 702) **仍独立**，未被吸入 571——它同样含实体「陈小春」，但剥离后与 571 各成员的字符重合度均 < 0.40（实测 0.13~0.17）。
  - **全量误合审计（同一快照前后对比）**：实体通道**仅新增 4 处合并，均语义正确**——陈小春事件、`肖战演唱会`票务事件、`终末地`游戏、wallstreetcn`韩国指数熔断`；**无新增误合**。簇数 **275 → 271**，跨平台簇 **14 → 13**（因 571 由 3 平台簇并入后计数口径变化）。实测 `_specific_span(429/车间…)=工人长相`、`_entity_rule(429/702)=False`。
  - **反事实评估**：`攻略→角色pv` 影响 **0 话题 / 0 平台** → 按 Q1-B 保留并落低优先级；`asml→阿斯麦`、`ubs→瑞银` 在当前快照影响 **0/0**（该次合并的 2 条已随榜单滚动离开快照，属正常）。
  - **迁移自愈**：旧库执行 `init_db` 后 `PRAGMA table_info(entity_alias_suggest)` 确认新增 `impact_topics`/`impact_platforms` 两列存在；`mine.run` 实测 `impact_scored=3`（覆盖 pending+approved 全部 3 行）。
- **运维提示**：本次修改 `align.py`/`alias.py`/`mine.py`/`runner.py`（调度/加工进程代码），已按约定（见 #13）**重启 `--schedule` 采集进程**；修改了 `web.py`/`index.html`，需**重启看板进程**生效。

---

### #23 · 2026-10-15 · 实体归一补召回（长公共子串兜底）+ 别名挖掘挂入加工周期

- **变更点**：
  1. **`align.py` 新增「长公共子串」兜底判据**（bigram-Jaccard 之外的第二条合并通道）。长标题会稀释 bigram-Jaccard，使同一事件在不同平台的措辞被切开。新增 `_lcs()`（`difflib` 最长公共连续子串）与 `_lcs_rule()`，判据含三重护栏：公共子串长度 ≥ `_LCS_MIN`(5)、**至少含 `_LCS_CJK_MIN`(3) 个汉字**（挡掉 openai/anthropic 等纯外文品牌名导致的跨事件误合）、**不含数字**（挡掉 "5万亿美元""5年期国债收益率" 等数量/指标表述）、且占较短标题比例 ≥ `_LCS_RATIO_MIN`(0.35)（挡掉 "如何评价这""指数日内跌" 等通用框定语稀释出的误合）。候选对循环中：Jaccard 不达阈值时，改由该兜底规则判定。
  2. **兜底判据作用在「原始 `norm_title`」而非别名改写键**：`alias.rewrite()` 会为命中同一规范词的标题**追加相同的规范锚点后缀**（`ANCHOR_SEP + 规范词 + ANCHOR_SEP`，如凡出现 "ai" 者末尾都带 "人工智能\x01"）。该后缀是人为共享片段、特异性弱，若参与 LCS 会把 "凌晨解禁的Gemini4…AI""一个教授为什么拥抱 AI""如何从零开始学习AI" 三条**彼此无关**的话题误并为同一簇（实测公共子串 "人工智能\x01人工智能\x01"=10 字、含 8 个汉字，恰好越过护栏）。故 LCS 只看自然标题文本。
  3. **`runner.py`：把别名挖掘挂入加工周期**。`enrich()` 末尾新增 `mine.run(conn)` 调用（对齐/融合/预警之后，此时 `topic_cluster` 已就绪供同簇挖掘），并在加工日志打印 `[别名] 扫描 N 项 → 新增候选 M 条`。此前 `--mine` 仅为手动 CLI 子命令，**从未挂入 `--schedule` 调度循环**，导致开启开采后候选不再增长。
- **涉及文件**：`app/align.py`、`app/runner.py`、`CHANGELOG.md`。
- **原因**：
  - 实体归一存在**跨平台漏合**：用户实测三条同事件标题（"工人撞脸陈小春拍摄者因舆论离职"、"撞脸陈小春工人：没有被抱养"、"撞脸陈小春工人否认是其弟弟"）Jaccard 仅约 0.24/0.25/0.35，均低于阈值 0.4，但共享 "撞脸陈小春"（LCS=5）——纯 Jaccard 无法识别。
  - 别名候选**不再持续新增**：根因是 `--mine` 未进调度，数据虽持续增长但从未再次挖掘；且 `entity_alias_suggest` 用 `INSERT OR IGNORE` 具状态粘性，已审状态不会被重置（此为期望行为，仅需保证「定期重挖」）。
- **验证**：
  - **实体归一（同一快照实测）**：目标三条标题修复前分属不同簇；修复后归入**同一簇（簇 571，共 5 条）**——`工人撞脸陈小春 拍摄者已离职`(baidu) / `撞脸陈小春工人：没有被抱养`(baidu) / `车间工人长相撞脸陈小春`(baidu) / `撞脸陈小春工人否认是其弟弟`(toutiao) / `工人撞脸陈小春拍摄者因舆论离职`(weibo)。
  - **误合排查（全量多成员簇复核）**：修复后全部多成员簇语义一致（异环明音凛、Claude/Anthropic、DeepSeek、肖战演唱会、深圳APEC、鸭绿江大桥、Gemini4 Argon、韩国指数、广交会、李嘉诚传闻、李秀满新团等）；此前由锚点后缀触发的 **AI 三条误合簇已消失**。簇数 **293 → 277**（同一快照预演，LCS 规则新增 28 对合并；线上因榜单滚动会有小幅波动）。
  - **别名挖掘挂载**：单次 `enrich` 实测新增 `[别名] 扫描 3 项 → 新增候选 1 条（pending 合计 1 条）`；分解为 `mine_pattern` 2（`ubs→瑞银`、`asml→阿斯麦`，已在词典故被过滤）+ `mine_extid` 0 + `mine_cluster` 1。
  - **候选不增长根因（数据侧）**：现有挖掘器为**高精度**设计——`mine_pattern` 只认显式句式（全称（简称）/X简称Y/X又称Y），787 条历史标题中仅 2 条命中；`mine_extid` 需同平台同 `external_id` 不同标题（本轮 0）；`mine_cluster` 从同簇成员差异片段提取（本轮 1，且质量偏弱如 `攻略→角色pv`）。故候选增速天然偏低，且**仅在新数据出现新句式时**才新增。
- **运维提示**：本次修改 `align.py`、`runner.py`（服务进程代码），已按约定（见 #13）**重启 `--schedule` 采集进程**生效；`web.py` 未改，看板无需重启。`--mine` 手动入口保留，行为与自动挂载一致（均为 pending、待人工审核，不自动启用）。

---

### #22 · 2026-10-15 · 别名候选审核支持「撤销」（已通过 / 已驳回可退回待审）

- **变更点**：
  1. **审核接口新增 `revoke` 动作**（`api_review`，原只支持 approve/reject）。
  2. **看板「别名候选」页**：已通过/已驳回行的「操作」列由占位「—」改为「撤销」按钮（新增 `.btn.warn` 样式）；页签说明文案补充撤销口径。
- **涉及文件**：`app/web.py`、`app/templates/index.html`。
- **原因**：此前审核不可逆（通过即写词典、驳回即终态），误判只能直接改库；且需支持人工在页面自行点选/收回，故补可逆入口。
- **行为口径**：撤销后候选 `status` 回到 `pending`；若该候选此前经审核写入正式词典（`entity_alias.source='auto'`），一并**删除该条目**；种子词典条目（非 auto）不受影响。审核只改词典数据、不触发重新对齐，下次加工（enrich）生效——与 approve/reject 一致。
- **验证**：
  - **HTTP（localhost:8000）**：`revoke ubs` → `status=pending`，`entity_alias` 中 `ubs` 条目被移除，`counts {approved:1, pending:1}`；随后 `approve ubs` 复原为 `approved`，`counts {approved:2}`。
  - **浏览器实测**：已通过行确实渲染出「撤销」按钮且不再显示「通过/驳回」；点击 `ubs→瑞银` 行的「撤销」后，已通过数 2→1，该行转入「待审」并恢复显示「通过/驳回」；全程无弹窗、控制台无报错。测试后已复原为 `approved`。
- **运维提示**：本次修改 `web.py`，已按约定（见 #13）重启看板进程生效。

---

### #21 · 2026-10-15 · 交叉语种别名抽取 + 真实闭环实跑（采集 → 挖掘候选 → 审核 → 写入词典）

- **变更点**：
  1. **`mine.py` 新增交叉语种括号抽取**：此前句式抽取只认「两侧共享汉字」的缩写关系，无法识别**中文全称 ↔ 拉丁简称/英译**（如 瑞银（UBS）、阿斯麦（ASML））——这类两侧无共同汉字的真实别名会被 `_overlap`/`_trim_left` 直接挡掉。新增 `_LATIN_TOKEN`（纯拉丁/数字且含字母）、`_CJK_ONLY`（纯汉字）、`_BOUNDARY_CHARS`（将/把/被/对/向/给/称/报/等/与/和/及/或/的），以及 `_trim_cjk_context()`（按最后一个边界字剥掉中文侧上下文，如「摩根大通将阿斯麦」→「阿斯麦」）与 `_cross_script_pair()`；`_pattern_pairs()` 命中交叉语种即单独处理（拉丁侧作别名、中文侧作规范词，方向与种子词典的「英译」一致）。纯英文全称（含空格，如 `Masan High-Tech Materials`）不匹配，自动排除。
  2. **真实闭环实跑（数据操作，非代码）**：按 `config/sources.toml` 现有配置，`python -m app.runner --schedule` 持续采集并真实写入正式库；`--mine` 产出候选（pending）；经看板 `POST /api/suggest/review` 审核通过后写入正式词典 `entity_alias`（`enabled=1`、`source='auto'`），再重跑加工令其生效。
- **涉及文件**：`app/mine.py`、`CHANGELOG.md`（数据变更落在正式库 `data/hotspot.db`）。
- **原因**：现有挖掘规则对中文↔拉丁别名存在**召回缺口**，导致真实数据下 `--mine` 长期为 0、审核闭环空转；需补齐交叉语种识别，才能让「采集 → 挖掘 → 人工审核 → 写入词典」在真实数据上跑通。
- **验证**：
  - **纯函数回归**：
    - 命中：`瑞银（UBS）`→`ubs→瑞银`；`摩根大通将阿斯麦（ASML）`→`asml→阿斯麦`。
    - 噪声被挡：`（早盘）`（混合语种全称）、`（彭博）`/`（央视新闻）`（来源署名）、`（强度）`（非实体括号）、含空格英文全称 → 均无输出。
    - 原回归不变：`碳达峰碳中和（双碳）`、`上海证券交易所简称上交所` 仍命中。
  - **真实采集**：runs **39 → 60**，榜单条目 **1762 → 2698**（本轮为 2026-10-15 实时快照，7 平台全 OK）。
  - **候选产出**：`--mine` 扫描 **2** → 新增 **2** 条 pending（`asml→阿斯麦`、`ubs→瑞银`，来源均为「句式抽取」）。
  - **审核写入**：`entity_alias` **62 → 64**（`asml→阿斯麦`、`ubs→瑞银`，`kind=auto`、`enabled=1`、`source='auto'`）；`entity_alias_suggest` 两条置 `approved`，**pending 0**。
  - **生效验证**（重跑 `enrich`）：别名命中 **32 → 34**；`topic_cluster.matched_alias` 实测 `asml × 2`、`ubs × 1`；话题簇 **293 → 292**（1 个跨平台合并）；跨平台簇 **13 → 14**。
- **运维提示**：本次未改 `web.py`，看板无需重启；已另起常驻进程 `python -m app.runner --schedule`（持续采集）与 `python -m app.web --port 8000`（看板）。

---

### #20 · 2026-10-14 · 看板「数据源配置」页：可视化编辑开关 / 间隔 / 重试并写回 sources.toml

- **变更点**：
  1. **`config.py` 新增写回能力**：`build()` 从看板提交数据构建并**校验** `Config`（`code` 非空且不重复、`adapter` 必须在 `adapters.ADAPTERS` 中已实现、`interval_sec≥5`/`max_retries≥0`/`retry_delay_sec≥0`/`enrich_interval_sec≥10`），非法即抛 `ValueError`；`dumps()` 手写 TOML 序列化（stdlib `tomllib` 只读，无 `tomli_w` 依赖），字符串用 JSON 转义保证可回读；`save()` 写回文件；`available_adapters()` 供前端选择器。
  2. **`web.py` 新增两个接口**：GET `/api/config`（返回配置路径、`enrich_interval_sec`、默认值、可用适配器、各源开关/间隔/重试）；POST `/api/config`（校验 → 写回 `config/sources.toml` → `sync_platforms()` 同步 `platform` 表）。`do_POST` 改为走 `POST_ROUTES` 路由表（新增 `/api/config`，与既有 `/api/suggest/review` 并列），错误统一 400。
  3. **`index.html` 新增「数据源配置」页签**：工具栏（加工节流输入 + 保存/重新载入 + 配置文件路径）；8 列表格（启用复选框 / 代码 / 名称 / 适配器 / 间隔 / 重试次数 / 重试等待 / 删除）；「新增数据源」行（代码/名称/适配器下拉/间隔 + 添加）；保存后回写并刷新顶部统计与榜单页签。新增 `input,select` 深色主题样式与 `.cfgbar`。
- **涉及文件**：`app/config.py`、`app/web.py`、`app/templates/index.html`、`CHANGELOG.md`。
- **原因**：此前开关/间隔/重试只能手工编辑 `config/sources.toml`；需在页面上直接查看与调整，且仍以配置文件为唯一事实来源（页面只做读写入口，不引入第二套配置）。
- **验证**：
  - 纯函数：`load()→dumps()→tomllib.loads()` 回读一致（7 源、`weibo` 间隔 180 保留）；坏适配器 `tok` → `ValueError`；`interval_sec=1` → `ValueError`（<5）；`sort_order` 缺省自动补 `i+1`。
  - 接口（curl/urllib 实测）：GET `/api/config` 返回完整结构（`path`、`enrich_interval_sec=600`、7 个可用适配器、7 源）；POST 坏适配器 → **400**+错误文案；POST 合法（`weibo` 停用 + 间隔 66、`enrich=99`）→ **200**、`sync={configured:7,enabled:6,disabled_removed:0}`；文件确实写入（`interval_sec = 66`、`enrich_interval_sec = 99`）；`platform` 表镜像同步（`weibo` `enabled` 1→0、`interval_sec` 180→66），其余不变。
  - 浏览器实测（端口 8000）：页签渲染正常，8 列表头齐全、7 行数据（`weibo`/名称「微博热搜」/`adapter=weibo`/间隔 180）；「新增数据源」行、适配器下拉 7 项、按钮均在位；**无控制台错误、无布局重叠**。
  - 测试后已还原 `config/sources.toml` 并重新同步（7 源全启用、`weibo` 间隔 180）。
  - 服务重启提示：`web.py` 有路由变更，需重启 `python -m app.web` 生效（沿用 #13 约定）；调度进程改配置后需重启 `--schedule` 生效（页面已注明）。

---

### #19 · 2026-10-14 · 配置驱动的定时采集：按源独立间隔 / 开关 / 失败重试（代码与数据源解耦）

- **变更点**：
  1. **新增 `config/sources.toml`**：数据源注册表，作为开关/间隔/重试策略的**唯一事实来源**。含 `[defaults]` 段（`enabled`/`interval_sec`/`max_retries`/`retry_delay_sec`/`enrich_interval_sec`）与 7 个 `[[source]]` 条目（`weibo` 间隔 180s、`baidu` 240s，其余默认 300s）。**新增数据源 = 写适配器 + 加一段 `[[source]]`**，无需改调度/采集代码。
  2. **新增 `app/config.py`**：`load()` 读 TOML 并逐项合并默认值（校验缺 `code`/重复 `code`），产出 `Source` 数据类与 `Config`；`sync_platforms()` 幂等把配置镜像进 `platform` 表，并把配置中**不存在/被注释**的平台自动置 `enabled=0`（**不删除**，保留历史留痕）。
  3. **新增 `app/scheduler.py`**：注入式 `Scheduler`，只消费 `Source` 规格与注入的 `collect_fn`；`run_forever()` 首采立即触发、随后**每源按各自 `interval_sec` 独立到期**触发（非全局固定间隔）；`_fire()` 实现失败重试（总尝试 = 1 + `max_retries`，每次间隔 `retry_delay_sec`，重试期间不阻塞其他源）；`next_due = 本次结束时刻 + interval` 防时间堆积；`stats()` 输出运行统计。
  4. **`schema.sql`**：`platform` 表新增 `interval_sec`/`max_retries`/`retry_delay_sec` 三列；**移除硬编码的平台种子 `INSERT OR IGNORE`**，改由配置同步写入；表头注释明确本表仅为配置的只读镜像。
  5. **`db.py`**：`init_db()` 追加上述三列的迁移（旧库自愈）。
  6. **`runner.py`**：新增 `--schedule`（按配置调度）、`--sources`（打印开关/间隔/重试表）、`--config`（自定义配置路径）；`bootstrap_config()` 加载配置并同步；`collect_with_retry()`/`run_cycle()` 的单次重试改由配置参数驱动；`--schedule` 模式接入 `Scheduler`（`tick` 取最小间隔，`enrich` 按 `enrich_interval_sec` 节流），Ctrl-C 优雅退出并打印各源采集/失败统计。
  7. **`web.py`**：新增 `_sync_sources()`，每个 API 请求前把配置同步进 `platform` 表（异常静默吞掉，不阻断看板），让看板平台名/启用态与配置一致。
- **涉及文件**：`config/sources.toml`（新增）、`app/config.py`（新增）、`app/scheduler.py`（新增）、`app/schema.sql`、`app/db.py`、`app/runner.py`、`app/web.py`、`CHANGELOG.md`。
- **原因**：原采集为全局固定间隔、平台清单硬编码，无法按平台调频、无法便捷停爬某平台、失败也不重试；需以配置驱动实现「按需开关 / 独立间隔 / 失败重试」，并让代码与数据源彻底解耦。
- **验证**：
  - 单测（假时钟 + 注入 collect_fn）：首采立即触发；各源按独立间隔到期；失败源按 `max_retries` 重试后最终成功/失败计数正确（`runs` 只在触发时计一次、`attempts` 计实际调用数）；enrich 按 `enrich_interval_sec` 节流；**注释/停用某平台**后配置不含该源、`platform.enabled` 自动置 0。
  - 命令行：`--sources` 输出 7 源开关/间隔/重试表正常（合计 7 · 启用 7）。
  - 实时 `--schedule` 短跑（`weibo` 调至每 5s，`badsrc` 适配器不存在）：真实抓取成功、坏源重试 2 次后最终失败，统计无串扰。
  - 旧库迁移与配置镜像：注释掉某平台后，`platform` 对应行 `enabled` 1 → 0，其余平台不受影响，历史留痕保留。
  - 服务重启提示：`web.py` 有改动（新增 `_sync_sources`），需重启 `python -m app.web` 生效（沿用 #13 约定）。

---

### #18 · 2026-10-14 · 看板「别名候选」页签 + 审核操作

- **变更点**：
  1. `web.py` 新增 GET `/api/suggest`（按 `status` 筛选，返回候选行 + 各状态计数）与 POST `/api/suggest/review`（`approve`/`reject`），`Handler` 新增 `do_POST`；`api_summary.counts` 新增 `suggests`（待审计数）。
  2. `index.html` 新增「别名候选」页签：状态筛选 chips（待审/已通过/已驳回/全部，带计数）+ 表格（别名/规范词/来源/证据/状态/操作）；`pending` 行带「通过/驳回」按钮；顶部统计条新增「别名候选待审」卡片。
  3. 审核语义：`approve` → 写 `entity_alias`（`enabled=1`、`source='auto'`）并置 `status=approved`；`reject` → 置 `status=rejected`。审核仅改词典数据，**不触发重新对齐**，下次采集/加工（`enrich`）生效。
- **涉及文件**：`app/web.py`、`app/templates/index.html`、`CHANGELOG.md`。
- **原因**：`entity_alias_suggest` 此前无查看/审核入口，候选只能靠 `--mine` 的样例输出或手写 SQL 查看，无法闭环「挖掘 → 人工审核 → 启用」。
- **验证**：
  - 接口（复制库注入候选）：`/api/suggest?status=pending` 返回候选与 `counts`；`approve` 后 `entity_alias` 出现 `enabled=1 / source='auto'` 且 suggest 置 `approved`；`reject` 置 `rejected`；非法 `action` 返回 **400**；`/api/summary.counts.suggests` 随审核联动（4→3）。
  - 浏览器实测（端口 8022，注入 4 条待审）：页签切换、6 列表格、筛选 chips 均正常；点「通过」→ 状态变「已通过」、操作列变「—」、待审 4→3 / 已通过 0→1、顶部统计联动；**无控制台错误、无布局问题**。
  - 服务重启提示：`web.py` 有路由变更，需重启 `python -m app.web` 才生效（沿用 #13 约定）。

---

### #17 · 2026-10-14 · 别名词典自动挖掘（候选灌入 entity_alias_suggest）

- **变更点**：
  1. **新增 `app/mine.py`**：从现有标题自动发现同义表述，候选写入 `entity_alias_suggest`（`status='pending'`，**不自动启用**）。三种来源：句式抽取（`全称（简称）`/`X简称Y`/`X又称·又名·亦称·即Y`）、extid 同源（同平台同 `external_id` 改题）、同簇共现（同话题簇成员比对）。
  2. `runner` 新增 `--mine` 开关，输出扫描数/新增数/来源分布与样例。
  3. **修复挖掘逻辑缺陷**：`_ok_pair` 误用未定义常量 `MAX_PAIR_LEN`（`NameError`）→ 改用 `MAX_ALIAS_LEN`/`MAX_CANON_LEN`；句式抽取正则贪心多吞上下文（如「简称」后黏「今日收盘」）且非实体用字黑名单误杀「碳达峰碳中和」（含"和"）→ 改为**两端按交集裁边 + 至少共享一字**；同簇共现由「最长公共子串左右截取」→「**公共前后缀对齐**」，并新增「共享边界字回填」以救回 我军↔解放军 这类被公共尾巴吞掉边界字的简称形态。
- **涉及文件**：`app/mine.py`（新增）、`app/runner.py`。
- **原因**：别名词典人工维护成本高；需从历史标题自动发现同义候选，人工审核后再进主词典 `entity_alias`。
- **验证**：
  - 纯函数回归：句式抽取命中 `双碳→碳达峰碳中和`、`上交所→上海证券交易所`（已种子覆盖，落库时跳过）；括号噪声 `（强度）`、`（彭博/路透）` 被挡。同簇共现命中 `我军→解放军`；`陆军/海军`（并列实体，等长）、`明日正式开通/将开通`（含虚词）、`盘锦/辽宁盘锦`（一侧为空）均被正确排除。
  - 端到端（复制库注入标题）：候选以 `status=pending`、`source=句式抽取` 落 `entity_alias_suggest`，`evidence` 保留原句。
  - 当前快照：扫描 **0** → 新增 **0**（无显式别名句式标题；簇内差异均为措辞/增删词，或已被种子词典覆盖，如簇 149 我军↔解放军）。

---

### #16 · 2026-10-14 · 盘点拆分的来源标记透出 + 子话题与单条去重

- **变更点**：
  1. **来源标记透出（①）**：`word_metric` 新增 `source` 列（`pipeline` 从 `hot_item.source` 透传）；`v_unified_word` 暴露 `来源` 列；`/api/unified` 与 `/api/latest` 返回该字段；看板「统一数据表」新增**来源**列、榜单页对子话题渲染「盘点拆分」徽标（新增 `.tag.split` 样式）。
  2. **去重（②）**：`split.expand` 在生成子话题前，收集同批次**非聚合单条条目**作参照，若子话题与之**等价（norm_title 相同）或 Jaccard 相似度 ≥ 0.6**，则丢弃该子话题（保留带真实热度的单条条目）；新增统计 `dropped`，`runner` 日志输出。
- **涉及文件**：`app/split.py`、`app/schema.sql`、`app/db.py`、`app/pipeline.py`、`app/web.py`、`app/templates/index.html`、`app/runner.py`。
- **原因**：① 盘点拆出的子话题会混入统一表/看板，需可辨识以免与正常条目混淆；② 同一新闻可能既在盘点内、又作为单条出现，造成同批次重复计数。
- **验证**：
  - 标记：`word_metric` 中 `source='split'` **6 行**、`v_latest_item` 中 **3 行**；`/api/unified` 返回含 `"来源"` 字段；看板列/徽标已就位。
  - 去重判定（构造样例）：与单条**等价** → 丢弃；**相似度 > 0.6** → 丢弃；**不同事件** → 保留。
  - 识别规则回归：`欧冠热门赛事亮点速览`、`王者十一周年庆福利爆料` 仍**不**判为聚合（无误伤）。
  - 当前快照 `dropped=0`（无重复对，属预期）。
- **汇总**：[拆分] 聚合条目 2 → 子话题 6 → 去重丢弃 0；`word_metric` 674 行 / 15 榜单。

---

### #15 · 2026-10-14 · 盘点/聚合条目识别与拆分（数据清洗）

- **变更点**：
  1. 新增 `app/split.py`：`detect()` 识别「一题多事」的盘点条目（强分隔符 `；;丨|` 计数 ≥2，或命中栏目名白名单 `8点1氪/早报/晚报/…`）；`split_title()` 剥栏目前缀后按强分隔符切分；`expand()` 把子话题写回 `hot_item`（`source='split'`，带 `parent_norm_title`/`seg_index`），幂等重算。
  2. `hot_item` 新增 5 列：`source`、`is_aggregate`、`aggregate_reason`、`parent_norm_title`、`seg_index`；`v_latest_item` 一并暴露这些列；`db.py` 迁移补齐（旧库自愈）。
  3. `pipeline` 归一化时排除父条目（`WHERE is_aggregate=0`）；`align` 与 `alerts` 话题扫描同样排除父条目；子话题正常参与归一化/对齐/融合/预警。
  4. 子话题 `hot_value` 置空 → 归一化退化为**纯榜位分**（继承父条目榜位）；`runner.enrich` 新增「拆分」步骤并输出统计。
- **涉及文件**：`app/split.py`（新增）、`app/schema.sql`、`app/db.py`、`app/pipeline.py`、`app/align.py`、`app/alerts.py`、`app/runner.py`。
- **原因**：盘点栏目（如 36氪 `8点1氪丨茶百道…；苹果…；DeepSeek…`）作为单条入库会同时导致：分类失真（整条只挂一个标签）、跨平台对齐失效（长 blob 算不出相似度）、热度口径失真、时序列污染（栏目每天固定出现）。
- **验证**：
  - 识别规则**只用强分隔符 + 栏目白名单**，避免误伤——`欧冠热门赛事亮点速览`、`王者十一周年庆福利爆料` 均未被判为聚合。
  - 全量重建：[拆分] 聚合条目 **2 条 → 拆出子话题 6 条**；`8点1氪` 在 `word_metric` / `topic_cluster` 行数 = **0**（已排除）；子话题各自分类（`茶百道→财经`、`DeepSeek→科技`）并进入归一化表与话题簇（簇 300/301/302）。
  - 看板接口 `/api/summary`、`/api/clusters`、`/api/entities` 正常返回。
- **已知代价**：同一新闻可能重复出现（既在盘点内、又在别处单条）；子话题继承父条目榜位，热度为纯榜位分（非真实热度）。

---

### #14 · 2026-10-14 · 建立本变更日志文档

- **变更点**：新增本 `CHANGELOG.md`，补齐此前每一次变更点，并确立后续「每次变更即时追加」的维护约定（见上）。
- **涉及文件**：`CHANGELOG.md`（新增）。
- **原因**：变更分散在多轮实现中，缺少单一可追溯的台账；后续仍需持续记录。
- **验证**：本文件生成并纳入后续改动流程。

---

### #13 · 2026-10-14 · 修复看板「统一数据表 / 跨平台话题 / 爆点预警」页签无数据

- **变更点**：定位为**旧服务进程残留**——上一个会话遗留的服务进程（8000、8011 端口）启动时代码较旧，缺少 `/api/unified`、`/api/entities` 路由，接口返回 `404 {"error":"not found"}`，前端拿不到数据即空白。处理：停掉遗留进程，用当前代码在 8000 重启干净实例。
- **涉及文件**：无代码改动；仅进程管理（停旧进程 + 重启 `app.web`）。
- **原因**：`web.py` 新增路由后未重启，旧进程不会加载新代码；而 `index.html` 按请求实时读取，导致「前端新、后端旧」错配。
- **验证**：旧进程 `/api/unified` 实测返回 `{"error":"not found"}`；新进程下 `/api/unified`、`/api/clusters`、`/api/alerts`、`/api/entities` 全部 200 且有数据。**结论：非前端 bug。**

---

### #12 · 2026-10-14 · 实体别名词典层（同义表述 → 同一实体）

- **变更点**：
  1. 新增 `entity_alias`（启用词典）与 `entity_alias_suggest`（自动挖掘候选、待审）两张表，并内置 **62 条种子词典**（简称/英译，覆盖军队、监管机构、经济指标、体育、游戏、健康、会展）。
  2. 新增 `app/alias.py`：`load_alias_map()` 读取词典；`rewrite()` 把别名改写成规范词，并注入**规范锚点 token**；ASCII 别名（如 `ai`/`nba`）编译为**带词边界的正则**，避免 `ai` 命中 `baidu`。
  3. `align.py` 接入：用「改写后的实体键」参与 bigram/Jaccard（存储仍用原 `norm_title`，保证簇 id 复用稳定）；新增 **`external_id` 硬规则**（同平台 + 同 `external_id` 直接判同实体）；`topic_cluster` 落 `matched_alias` 审计列。
  4. `db.py` 新增幂等迁移 `_ensure_column()`，给旧库补 `matched_alias` 列。
  5. `runner.py` 加工日志输出「别名命中 N 条」。
- **涉及文件**：`app/schema.sql`、`app/alias.py`（新增）、`app/align.py`、`app/db.py`、`app/runner.py`。
- **原因**：原对齐仅靠 bigram 相似度，对「同义但不同措辞」无能为力（如「我军匕首枪曝光」vs「解放军演示匕首枪」Jaccard 仅 0.18 < 阈值 0.4，无法合并）。
- **验证**：别名改写后该对 Jaccard 0.18 → 0.40（越过阈值）→ 加锚点 0.47；重建后**别名命中 34 条**，目标样例成功合并入同一簇（簇 149：baidu `解放军演示匕首枪` + toutiao/weibo `我军匕首枪曝光`，平台覆盖 2 → 3）；总簇 292 → 291（恰好 1 个预期合并）；跨平台簇中「分类不一致」的簇 = 0（**无误合并**）。

---

### #11 · 2026-10-14 · 跨平台实体归一 + 同词融合

- **变更点**：新增实体级聚合表 `entity_metric` 与出口视图 `v_unified_entity`；新增 `app/fuse.py`，在话题簇内用各平台**归一化热度**（而非不可比的原始热度）融合出实体级聚合热度，**双口径并列**产出：峰值主导式 `agg_peak = clamp(max(s_i) + 800·ln(1+平台数-1), 0, 10000)`、联合曝光式 `agg_joint = clamp(10000·(1-∏(1-s_i/10000)), 0, 10000)`；`runner.enrich` 插入「对齐 → 融合 → 预警」；看板新增 `/api/entities` 与「实体归一」标签页。
- **涉及文件**：`app/schema.sql`、`app/fuse.py`（新增）、`app/runner.py`、`app/web.py`、`app/templates/index.html`。
- **原因**：此前的「跨平台话题」只做对齐（哪些平台在讲同一件事），其 `heat_sum`/`peak_hot` 仍用原始热度求和，把「搜索热度」和「讨论热度」相加无意义；且未打通统一标准表。
- **验证**：产出**实体 292 个（其中跨平台 13 个）**；双口径差异符合设计（如 3 平台簇 峰值式 9053 / 联合式 9800）；看板「实体归一」页浏览器实测：10 列表头齐全、200 行、平台名 pill 正常、无乱码/空态。

---

### #10 · 2026-10-14 · 分类逻辑优化（降低「其他」占比）

- **变更点**：`lexicon.py` 算法改为**最长匹配优先 + 片段屏蔽 + 平局消解**（命中一个词后从待匹配串剔除，避免子串重复计分；得分相同时比较各类最长命中关键词）；新增「时政」「游戏」两个分类；关键词从 ~150 条扩充至 ~600 → ~660 条；从广告词表中移除「广告」，避免误过滤正常新闻标题（如「舒客广告被罚」）。
- **涉及文件**：`app/lexicon.py`。
- **原因**：「其他」占比过大，部分条目明显可分入既有标签（如「解放军演示匕首枪」应为「军事」）。
- **验证**：「其他」181 → 47 → 43（占比 6%）；「解放军演示匕首枪」等正确映射到「军事/时政/游戏/财经/科技」；抽验无新增误分类。当前分布：科技 116 · 财经 113 · 社会 105 · 娱乐 66 · 其他 43 · 游戏 40 · 国际 38 · 体育 35 · 健康 35 · 时政 26 · 教育 25 · 军事 15 · 汽车 13。

---

### #9 · 2026-10-14 · 适配器与视图修复批次

- **变更点**：
  1. `adapters.py` 补 `metric_type` 热度口径标注（讨论热度/搜索热度/热度值/阅读量/重要度/榜位）。
  2. 百度接口从 `wise` 切到 `pc`，恢复数值热度（`wise` 不再返回数值，曾导致百度只能纯榜位归一）。
  3. `cn_number` 兼容直接传入 `int`（修复 bilibili/36kr/华尔街见闻解析时 `TypeError: int is not iterable`）。
  4. 修复 `v_latest_item` 视图 `no such column: run_id`：子查询改用 `MAX(id)`；视图统一 `DROP VIEW IF EXISTS ... CREATE VIEW` 保证自愈。
  5. 中文筛选参数（category/trend）改 URL 安全 base64url 传输，后端 `_decode_param` 兼容明文；修复 `/api/timeline` 中文被中间层按 latin-1 解析导致的 400。
- **涉及文件**：`app/adapters.py`、`app/schema.sql`、`app/web.py`。
- **原因**：跨平台归一化依赖可比的热度数值与正确的视图；中文参数经中间层易被破坏。
- **验证**：7 平台采集全部成功；百度恢复数值热度；中文筛选与话题时间线弹窗通过浏览器实测。

---

### #8 · 2026-10-14 · 统一数据 Schema + 数据清洗/归一化打标

- **变更点**：新增 `app/pipeline.py`，实现**榜内对数分位 + 榜位融合**归一化（`norm_score = 7000×对数分位 + 3000×榜位权重`；无原始热度时退化为 `10000×榜位权重`），产出跨平台可比热度 0~10000；新增统一标准表 `word_metric`、基准留痕表 `norm_basis`、统一出口视图 `v_unified_word`；`runner` 支持 `--rebuild` 全量重建；看板新增「统一数据表」标签页、行业筛选条、归一化热度条形图。
- **涉及文件**：`app/pipeline.py`（新增）、`app/schema.sql`、`app/runner.py`、`app/web.py`、`app/templates/index.html`。
- **原因**：补齐用户要求的「统一数据 Schema」与「数据清洗 & 归一化打标」两项能力。
- **验证**：`word_metric` 正常产出；`v_unified_word` 输出 8 列业务口径；报表新增「统一标准表 Top12」与「分类分布」；页面标签页实测可用。

---

### #7 · 2026-10-14 · 网页看板

- **变更点**：新增 `app/web.py`（内置 HTTP 服务，暴露 `/api/summary`、`/api/latest`、`/api/clusters`、`/api/alerts`、`/api/timeline`）与单页 `templates/index.html`（4+1 标签页：最新榜单/跨平台话题/爆点预警/采集健康度 + 隐藏的统一数据表；话题时间线弹窗）。
- **涉及文件**：`app/web.py`（新增）、`app/templates/index.html`（新增）。
- **原因**：需要把采集与分析结果可视化展示。
- **验证**：浏览器实测标签页切换、话题行点击弹窗均正常。

---

### #6 · 2026-10-14 · 爆点预警

- **变更点**：新增 `app/alerts.py`，实现冷启动护栏、冷却去重、多信号加权评分（热度飙升 / 名次跃升 / 新上榜 / 跨平台扩散），分级输出 high/medium/low 预警；新增 `alert` 表与索引。
- **涉及文件**：`app/alerts.py`（新增）、`app/schema.sql`。
- **原因**：需要在热点爆发时给出分级预警。
- **验证**：分级合理，与跨平台扩散信号联动。

---

### #5 · 2026-10-14 · 跨平台话题对齐

- **变更点**：新增 `app/align.py`，用字符 bigram 倒排索引 + Jaccard 相似度 + 并查集合并为「话题簇」；簇 id 增量复用（新簇与旧快照成员重叠则沿用旧 id）；新增 `topic_cluster` 表与 `v_cluster` 视图。配套 `app/normalize.py` 做标题归一化（全角转半角/去标点/小写）与中文数值解析。
- **涉及文件**：`app/align.py`（新增）、`app/normalize.py`（新增）、`app/schema.sql`。
- **原因**：需要把不同平台上描述同一事件的话题聚合到一起。
- **验证**：实跑产出多个跨平台簇，簇 id 跨批次稳定。

---

### #4 · 2026-10-14 · 免费接口实跑打通

- **变更点**：接入并实跑 7 个免费公开热榜接口：微博热搜、百度热搜、知乎热榜、头条热榜、B站热搜、36氪热榜、华尔街见闻。
- **涉及文件**：`app/adapters.py`。
- **原因**：MVP 需先在零授权成本下拿到可用数据。
- **验证**：7 个接口全部实跑通过并入库。

---

### #3 · 2026-10-14 · MVP 骨架：适配器层 + 采集 runner + 历史留痕库

- **变更点**：新增 `app/db.py`（SQLite 访问层，WAL/外键/批量写）、`app/runner.py`（单次/循环/报表/清理 CLI）、初始 `schema.sql`；初始库结构为 **4 表**（`platform` / `collect_run` / `raw_payload` / `hot_item`）、**4 视图**（`v_latest_item` / `v_topic_lifecycle` / `v_rank_timeline` / `v_run_health`）、**6 索引**；原始响应留痕可审计。
- **涉及文件**：`app/db.py`（新增）、`app/runner.py`（新增）、`app/schema.sql`（新增）。
- **原因**：落地「免费接口 + 历史留痕数据库」的 MVP 架构。
- **验证**：采集、入库、同一话题跨批次追踪、榜单无变化检测均验证通过。

---

### #2 · 2026-10-14 · 数据源调研与分级

- **变更点**：调研并分级可用数据源：免费公开接口 / 官方开放平台 / 第三方聚合 API / SaaS 订阅，给出名称、接口、获取方式与收费量级，形成可落地的组合方案。
- **涉及文件**：无（分析产出，结论汇入 #3、#4 的实现）。
- **原因**：为 MVP 选型与后续扩展提供依据。
- **验证**：结论直接支撑了 #4 的 7 接口实跑。

---

### #1 · 2026-10-14 · 竞品逆向与算法复现（soureci.com）

- **变更点**：抓包 `https://www.soureci.com/` 完成数据来源分析；复现其 32 位哈希函数 `H(s) = abs((h<<5) - h + charCode)`；确认其 platform/industry/crowd/gender/spark/dup 等指数化维度为**纯哈希派生、无真实统计**。
- **涉及文件**：无（分析产出）。
- **原因**：需要判断竞品的价值与可复制性，作为自建产品的参照。
- **验证**：算法确定性复现成功，维度生成逻辑得到解释。
