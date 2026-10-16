-- =============================================================
-- 热点聚合 MVP · 历史留痕数据库 schema (SQLite)
-- 设计目标：
--   1) 每个平台每次采集 = 一个 collect_run（批次），形成不可变的时间序列
--   2) 批次内的每一条榜单条目 = 一行 hot_item（即"历史留痕"原始事实表）
--   3) 原始响应 body 单独留存，便于审计/回溯/复算
--   4) 通过视图派生"话题生命周期""排名时间线"等分析口径
-- =============================================================

PRAGMA foreign_keys = ON;

-- ---------- 平台元数据 ----------
-- 注意：平台清单不再硬编码于此，改由 config/sources.toml 提供（见 app/config.py）。
-- 本表是配置的只读镜像，供看板/报表展示；启动时由 config.sync_platforms() 写入。
CREATE TABLE IF NOT EXISTS platform (
  code             TEXT PRIMARY KEY,      -- 平台代码：weibo/baidu/...
  name             TEXT NOT NULL,          -- 中文名
  adapter          TEXT NOT NULL,          -- 适配器标识（app/adapters.py 的 ADAPTERS 键）
  enabled          INTEGER NOT NULL DEFAULT 1,
  sort_order       INTEGER NOT NULL DEFAULT 0,
  interval_sec     INTEGER NOT NULL DEFAULT 300,   -- 抓取间隔（秒）
  max_retries      INTEGER NOT NULL DEFAULT 2,     -- 失败后额外重试次数
  retry_delay_sec  INTEGER NOT NULL DEFAULT 5,     -- 重试等待（秒）
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- 采集批次 ----------
-- 每次对一个平台的抓取动作产生一批；用于记录运行状态、耗时、响应指纹
CREATE TABLE IF NOT EXISTS collect_run (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  platform_code TEXT NOT NULL,
  started_at    TEXT NOT NULL,                 -- ISO8601（本地时区）
  finished_at   TEXT,
  status        TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','empty','failed')),
  http_status   INTEGER,
  item_count    INTEGER NOT NULL DEFAULT 0,
  duration_ms   INTEGER,
  payload_hash  TEXT,                          -- 原始响应 sha256，用于判断榜单是否变化
  changed       INTEGER NOT NULL DEFAULT 1,    -- 相对上一次同平台内容是否变化
  error         TEXT,
  FOREIGN KEY (platform_code) REFERENCES platform(code)
);
CREATE INDEX IF NOT EXISTS idx_run_platform_time ON collect_run (platform_code, started_at);
CREATE INDEX IF NOT EXISTS idx_run_status        ON collect_run (status);

-- ---------- 原始响应留痕（可审计） ----------
CREATE TABLE IF NOT EXISTS raw_payload (
  run_id        INTEGER PRIMARY KEY,
  platform_code TEXT NOT NULL,
  body          TEXT,                          -- 原始响应全文（JSON/文本）
  length        INTEGER,
  captured_at   TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES collect_run(id) ON DELETE CASCADE
);

-- ---------- 榜单条目（核心事实表 / 历史留痕） ----------
CREATE TABLE IF NOT EXISTS hot_item (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        INTEGER NOT NULL,
  platform_code TEXT NOT NULL,
  position      INTEGER,                       -- 榜单展示位次（1-based，含置顶）
  is_top        INTEGER NOT NULL DEFAULT 0,    -- 是否置顶
  title         TEXT NOT NULL,                 -- 原始标题
  norm_title    TEXT NOT NULL,                 -- 归一化标题（去重/跨批次匹配键）
  external_id   TEXT,                          -- 平台内唯一ID（知乎 question id、头条 ClusterId…）
  url           TEXT,
  hot_value     INTEGER,                        -- 数值化热度（可空）
  hot_text      TEXT,                           -- 原始热度文本（如 "708 万热度"）
  label         TEXT,                           -- 标签（热/新/爆…）
  author        TEXT,
  extra_json    TEXT,                           -- 平台特有字段（点赞/评论/阅读…）
  -- 盘点/聚合类条目处理（由 split.py 加工生成）
  source            TEXT NOT NULL DEFAULT 'raw',  -- 事实来源：raw=原始榜单；split=盘点条目拆出的子话题
  is_aggregate      INTEGER NOT NULL DEFAULT 0,   -- 是否盘点类条目（一题多事，如 "8点1氪"）
  aggregate_reason  TEXT,                          -- 聚合判定理由
  parent_norm_title TEXT,                          -- 拆分来源：父条目归一化标题
  seg_index         INTEGER,                       -- 拆分来源：子话题序号（1-based）
  captured_at   TEXT NOT NULL,                  -- 采集时刻（冗余自 run，便于直接按时间查询）
  FOREIGN KEY (run_id) REFERENCES collect_run(id) ON DELETE CASCADE,
  FOREIGN KEY (platform_code) REFERENCES platform(code)
);
CREATE INDEX IF NOT EXISTS idx_item_run        ON hot_item (run_id);
CREATE INDEX IF NOT EXISTS idx_item_platform_norm ON hot_item (platform_code, norm_title);
CREATE INDEX IF NOT EXISTS idx_item_captured   ON hot_item (captured_at);
CREATE INDEX IF NOT EXISTS idx_item_extid      ON hot_item (platform_code, external_id);

-- =============================================================
-- 派生视图（不落表，按需查询即可）
-- =============================================================

-- 各平台"最新一次成功采集"的榜单
DROP VIEW IF EXISTS v_latest_item;
CREATE VIEW v_latest_item AS
SELECT
  h.platform_code, h.run_id, h.position, h.is_top, h.title, h.norm_title, h.external_id,
  h.url, h.hot_value, h.hot_text, h.label, h.author, h.extra_json, h.captured_at,
  h.source, h.is_aggregate, h.aggregate_reason, h.parent_norm_title, h.seg_index
FROM hot_item h
JOIN (
  SELECT platform_code, MAX(id) AS rid
  FROM collect_run
  WHERE status = 'ok'
  GROUP BY platform_code
) r ON h.platform_code = r.platform_code AND h.run_id = r.rid;
-- 话题生命周期：同一话题（平台内）首次/末次出现、上榜批次数、最佳名次、峰值热度
DROP VIEW IF EXISTS v_topic_lifecycle;
CREATE VIEW v_topic_lifecycle AS
SELECT
  platform_code,
  norm_title,
  MAX(title)                       AS sample_title,
  MIN(captured_at)                 AS first_seen,
  MAX(captured_at)                 AS last_seen,
  COUNT(DISTINCT run_id)           AS seen_runs,
  MIN(position)                    AS best_position,
  MAX(hot_value)                   AS peak_hot,
  MAX(external_id)                 AS external_id
FROM hot_item
GROUP BY platform_code, norm_title;

-- 排名时间线：可直接用于画"热度/名次随时间变化"曲线
DROP VIEW IF EXISTS v_rank_timeline;
CREATE VIEW v_rank_timeline AS
SELECT
  platform_code, norm_title, external_id,
  run_id, captured_at, position, hot_value, label
FROM hot_item
ORDER BY platform_code, norm_title, captured_at;

-- 采集健康度：每平台最近运行结果
DROP VIEW IF EXISTS v_run_health;
CREATE VIEW v_run_health AS
SELECT
  platform_code,
  COUNT(*)                                          AS total_runs,
  SUM(CASE WHEN status = 'ok'    THEN 1 ELSE 0 END) AS ok_runs,
  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_runs,
  MAX(started_at)                                   AS last_run,
  ROUND(AVG(duration_ms))                           AS avg_ms
FROM collect_run
GROUP BY platform_code;

-- ---------- 平台数据 ----------
-- 平台清单由 config/sources.toml 提供，启动时经 app/config.py 的 sync_platforms() 写入。
-- 此处不再硬编码 INSERT，避免"代码/清单"双份维护。

-- =============================================================
-- 实体别名词典（同义表述 → 规范词）
-- =============================================================
-- 在跨平台对齐前，把标题里的别名改写成规范词，让同义但不同措辞的标题
-- 共享同一串 bigram，从而越过 Jaccard 阈值被合并为同一实体。
-- alias_norm / canonical 都必须是 normalize_title() 归一化后的形式
-- （全角转半角、去标点空格、小写），否则匹配不上。
CREATE TABLE IF NOT EXISTS entity_alias (
  alias_norm TEXT PRIMARY KEY,      -- 别名（归一化后）
  canonical  TEXT NOT NULL,         -- 规范词（归一化后）
  kind       TEXT,                  -- 简称/英译/别名
  weight     REAL DEFAULT 1.0,
  enabled    INTEGER NOT NULL DEFAULT 1,
  source     TEXT,                  -- seed/auto/manual
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alias_canonical ON entity_alias (canonical);

-- 自动挖掘的别名候选（人工审核后再 enabled 进主表），避免脏词污染主词典
CREATE TABLE IF NOT EXISTS entity_alias_suggest (
  alias_norm TEXT NOT NULL,
  canonical  TEXT NOT NULL,
  source     TEXT,                  -- 同簇共现/句式抽取/extid同源
  evidence   TEXT,                  -- 证据（样例标题或计数）
  impact_topics    INTEGER,         -- 反事实评估：若启用，将并入他簇的话题数（0=当前无跨平台影响）
  impact_platforms INTEGER,         -- 反事实评估：上述聚合覆盖的平台数
  status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (alias_norm, canonical)
);

-- 种子别名词典（简称/英译为主；INSERT OR IGNORE 保证幂等）
INSERT OR IGNORE INTO entity_alias (alias_norm, canonical, kind, source) VALUES
  -- 军队 / 国际组织
  ('我军',        '解放军',                  '简称', 'seed'),
  ('人民解放军',  '解放军',                  '别名', 'seed'),
  ('美军',        '美国军队',                '简称', 'seed'),
  ('俄军',        '俄罗斯军队',              '简称', 'seed'),
  ('乌军',        '乌克兰军队',              '简称', 'seed'),
  ('以军',        '以色列军队',              '简称', 'seed'),
  ('北约',        '北大西洋公约组织',        '简称', 'seed'),
  ('欧盟',        '欧洲联盟',                '简称', 'seed'),
  ('世卫',        '世界卫生组织',            '简称', 'seed'),
  ('世卫组织',    '世界卫生组织',            '别名', 'seed'),
  ('联合国安理会','联合国安全理事会',        '别名', 'seed'),
  -- 机构 / 监管部门
  ('美联储',      '美国联邦储备委员会',      '简称', 'seed'),
  ('央行',        '中国人民银行',            '简称', 'seed'),
  ('证监会',      '中国证券监督管理委员会',  '简称', 'seed'),
  ('银保监会',    '中国银行保险监督管理委员会','简称','seed'),
  ('上交所',      '上海证券交易所',          '简称', 'seed'),
  ('深交所',      '深圳证券交易所',          '简称', 'seed'),
  ('港交所',      '香港交易所',              '简称', 'seed'),
  ('发改委',      '国家发展和改革委员会',    '简称', 'seed'),
  ('工信部',      '工业和信息化部',          '简称', 'seed'),
  ('商务部',      '中华人民共和国商务部',    '简称', 'seed'),
  ('外交部',      '中华人民共和国外交部',    '简称', 'seed'),
  ('国防部',      '中华人民共和国国防部',    '简称', 'seed'),
  ('教育部',      '中华人民共和国教育部',    '简称', 'seed'),
  ('公安部',      '中华人民共和国公安部',    '简称', 'seed'),
  ('财政部',      '中华人民共和国财政部',    '简称', 'seed'),
  ('农业农村部',  '中华人民共和国农业农村部','简称', 'seed'),
  ('卫健委',      '国家卫生健康委员会',      '简称', 'seed'),
  ('医保局',      '国家医疗保障局',          '简称', 'seed'),
  ('国常会',      '国务院常务会议',          '简称', 'seed'),
  ('人大常委会',  '全国人民代表大会常务委员会','简称','seed'),
  -- 经济指标
  ('gdp', '国内生产总值',         '英译', 'seed'),
  ('cpi', '居民消费价格指数',     '英译', 'seed'),
  ('ppi', '工业生产者出厂价格指数','英译', 'seed'),
  ('pmi', '采购经理指数',         '英译', 'seed'),
  -- 科技
  ('ai',  '人工智能',             '英译', 'seed'),
  ('5g',  '第五代移动通信技术',   '英译', 'seed'),
  ('6g',  '第六代移动通信技术',   '英译', 'seed'),
  -- 体育
  ('nba',     '美国职业篮球联赛', '英译', 'seed'),
  ('cba',     '中国男子篮球职业联赛','英译','seed'),
  ('中超',    '中国足球协会超级联赛','简称','seed'),
  ('欧冠',    '欧洲冠军联赛',     '简称', 'seed'),
  ('英超',    '英格兰足球超级联赛','简称', 'seed'),
  ('西甲',    '西班牙足球甲级联赛','简称', 'seed'),
  ('意甲',    '意大利足球甲级联赛','简称', 'seed'),
  ('德甲',    '德国足球甲级联赛', '简称', 'seed'),
  ('世界杯',  '国际足联世界杯',   '简称', 'seed'),
  ('欧洲杯',  '欧洲足球锦标赛',   '简称', 'seed'),
  ('亚运会',  '亚洲运动会',       '简称', 'seed'),
  ('奥运会',  '奥林匹克运动会',   '简称', 'seed'),
  ('冬奥会',  '冬季奥林匹克运动会','简称', 'seed'),
  ('全运会',  '全国运动会',       '简称', 'seed'),
  -- 游戏 / 电竞
  ('lol',  '英雄联盟',            '英译', 'seed'),
  ('电竞', '电子竞技',            '简称', 'seed'),
  -- 健康
  ('新冠',    '新型冠状病毒',     '简称', 'seed'),
  ('甲流',    '甲型流感',         '简称', 'seed'),
  ('乙流',    '乙型流感',         '简称', 'seed'),
  ('猴痘',    '猴痘病毒',         '别名', 'seed'),
  ('埃博拉',  '埃博拉病毒',       '别名', 'seed'),
  -- 会展
  ('广交会',  '中国进出口商品交易会',        '简称', 'seed'),
  ('进博会',  '中国国际进口博览会',          '简称', 'seed'),
  ('服贸会',  '中国国际服务贸易交易会',      '简称', 'seed');

-- =============================================================
-- 跨平台话题对齐 + 爆点预警
-- =============================================================

-- 话题簇映射：每个 (平台, 归一化标题) 归属一个簇；每次对齐刷新快照
CREATE TABLE IF NOT EXISTS topic_cluster (
  cluster_id    INTEGER NOT NULL,
  platform_code TEXT NOT NULL,
  norm_title    TEXT NOT NULL,
  title         TEXT,
  similarity    REAL,                 -- 与簇内代表话题的相似度
  matched_alias TEXT,                 -- 本次对齐中命中的别名词（逗号分隔，用于审计）
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (platform_code, norm_title)
);
CREATE INDEX IF NOT EXISTS idx_cluster_id ON topic_cluster (cluster_id);

-- 预警记录：scope=topic（单平台话题）或 cluster（跨平台簇）
CREATE TABLE IF NOT EXISTS alert (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at    TEXT NOT NULL,
  scope         TEXT NOT NULL CHECK (scope IN ('topic','cluster')),
  platform_code TEXT,
  norm_title    TEXT,
  cluster_id    INTEGER,
  title         TEXT NOT NULL,
  level         TEXT NOT NULL CHECK (level IN ('high','medium','low')),
  score         REAL NOT NULL,
  signals_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_time  ON alert (created_at);
CREATE INDEX IF NOT EXISTS idx_alert_level ON alert (level);

-- 簇汇总视图：平台覆盖数、峰值/合计热度、最佳名次、代表标题
DROP VIEW IF EXISTS v_cluster;
CREATE VIEW v_cluster AS
SELECT
  c.cluster_id, c.platform_count, c.platforms,
  m.topic_count, m.peak_hot, m.heat_sum, m.best_position, m.label, m.updated_at
FROM (
  SELECT cluster_id, COUNT(*) AS platform_count, GROUP_CONCAT(pc) AS platforms
  FROM (SELECT DISTINCT cluster_id, platform_code AS pc FROM topic_cluster)
  GROUP BY cluster_id
) c
LEFT JOIN (
  SELECT tc.cluster_id,
         COUNT(*)            AS topic_count,
         MAX(li.hot_value)   AS peak_hot,
         SUM(li.hot_value)   AS heat_sum,
         MIN(li.position)    AS best_position,
         MAX(li.title)       AS label,
         MIN(tc.updated_at)  AS updated_at
  FROM topic_cluster tc
  LEFT JOIN v_latest_item li
    ON li.platform_code = tc.platform_code AND li.norm_title = tc.norm_title
  GROUP BY tc.cluster_id
) m ON m.cluster_id = c.cluster_id;

-- =============================================================
-- 数据加工层：统一标准表 + 归一化基准留痕
-- =============================================================

-- 统一标准表：一行 = 某平台 · 某词 · 某批次
-- 字段正好覆盖：词/来源平台/热度原始值/归一化热度/环比增长率/上榜时间/分类标签/趋势标记
CREATE TABLE IF NOT EXISTS word_metric (
  run_id          INTEGER NOT NULL,
  platform_code   TEXT NOT NULL,              -- 来源平台
  norm_title      TEXT NOT NULL,              -- 词（归一化键）
  title           TEXT,                       -- 词（原始）
  metric_type     TEXT,                       -- 热度口径：讨论热度/热度值/阅读量/重要度/榜位
  hot_value       INTEGER,                    -- 热度原始值
  position        INTEGER,                    -- 榜位
  norm_score      INTEGER,                    -- 归一化热度 0~10000  ← 核心
  prev_norm_score INTEGER,                    -- 上批次归一化热度
  heat_growth     REAL,                       -- 环比增长率
  rank_delta      INTEGER,                    -- 名次变化（正=上升）
  first_seen_at   TEXT,                       -- 上榜时间
  category        TEXT,                       -- 分类标签：财经/科技/娱乐/社会…
  category_conf   REAL,                       -- 分类置信度
  trend_flag      TEXT,                       -- 趋势标记：飙升/新晋/回落/稳定
  is_filtered     INTEGER NOT NULL DEFAULT 0, -- 是否被清洗过滤
  filter_reason   TEXT,                       -- 过滤原因
  source          TEXT NOT NULL DEFAULT 'raw',-- 来源：raw=原始榜单条目；split=盘点条目拆出的子话题
  captured_at     TEXT NOT NULL,
  PRIMARY KEY (run_id, platform_code, norm_title)
);
CREATE INDEX IF NOT EXISTS idx_wm_platform_norm ON word_metric (platform_code, norm_title);
CREATE INDEX IF NOT EXISTS idx_wm_run           ON word_metric (run_id);
CREATE INDEX IF NOT EXISTS idx_wm_score         ON word_metric (norm_score);
CREATE INDEX IF NOT EXISTS idx_wm_category      ON word_metric (category);
CREATE INDEX IF NOT EXISTS idx_wm_trend         ON word_metric (trend_flag);

-- 归一化基准留痕：记录每平台每批次归一化所用的口径与极值，保证可复算/可审计
CREATE TABLE IF NOT EXISTS norm_basis (
  run_id        INTEGER NOT NULL,
  platform_code TEXT NOT NULL,
  metric_type   TEXT,
  board_n       INTEGER,                      -- 该批次榜单条数
  v_min         INTEGER,                      -- 原始热度最小值
  v_max         INTEGER,                      -- 原始热度最大值
  method        TEXT,                         -- 归一化方法标识
  created_at    TEXT NOT NULL,
  PRIMARY KEY (run_id, platform_code)
);

-- 统一标准出口：正好输出业务方要的 8 列（取各平台最新批次，剔除被过滤词）
DROP VIEW IF EXISTS v_unified_word;
CREATE VIEW v_unified_word AS
SELECT
  w.title         AS 词,
  w.platform_code AS 来源平台,
  w.metric_type   AS 热度口径,
  w.hot_value     AS 热度原始值,
  w.norm_score    AS 归一化热度,
  w.heat_growth   AS 环比增长率,
  w.first_seen_at AS 上榜时间,
  w.category      AS 分类标签,
  w.trend_flag    AS 趋势标记,
  w.source        AS 来源
FROM word_metric w
JOIN (
  SELECT platform_code, MAX(run_id) AS rid FROM word_metric GROUP BY platform_code
) t ON t.platform_code = w.platform_code AND t.rid = w.run_id
WHERE w.is_filtered = 0;

-- =============================================================
-- 跨平台实体归一 + 同词融合（实体级聚合表）
-- =============================================================
-- 一行 = 一个实体（同一事件跨平台对齐后的簇），把各平台的"归一化热度"
-- 融合成一个聚合热度。与 topic_cluster 同为快照，每次加工时整体刷新。
-- 两种融合口径并列产出，便于对比与后续选型：
--   峰值主导式 agg_peak  = clamp(max(s_i) + K·ln(1+平台数-1), 0, 10000)
--   联合曝光式 agg_joint = clamp(10000·(1-∏(1-s_i/10000)), 0, 10000)
-- 其中 s_i 为各平台归一化热度（0~10000），K 为扩散加成系数。
CREATE TABLE IF NOT EXISTS entity_metric (
  entity_id       INTEGER PRIMARY KEY,      -- 实体 id（沿用 cluster_id）
  canonical_title TEXT,                     -- 代表标题（簇内归一化热度最高的成员）
  category        TEXT,                     -- 分类标签（代表成员）
  agg_peak        INTEGER,                  -- 聚合热度·峰值主导式 0~10000
  agg_joint       INTEGER,                  -- 聚合热度·联合曝光式 0~10000
  platform_count  INTEGER,                  -- 覆盖平台数
  platforms       TEXT,                     -- 覆盖平台（逗号分隔）
  max_norm_score  INTEGER,                  -- 单平台归一化热度峰值
  spread_bonus    REAL,                     -- 扩散加成 K·ln(1+平台数-1)
  best_position   INTEGER,                  -- 簇内最佳名次
  first_seen_at   TEXT,                     -- 上榜时间（簇内最早）
  trend_flag      TEXT,                     -- 趋势标记（代表成员）
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_peak  ON entity_metric (agg_peak);
CREATE INDEX IF NOT EXISTS idx_entity_joint ON entity_metric (agg_joint);
CREATE INDEX IF NOT EXISTS idx_entity_pc    ON entity_metric (platform_count);

-- 实体级统一出口：一行 = 一个实体（同词融合后），双口径并列
DROP VIEW IF EXISTS v_unified_entity;
CREATE VIEW v_unified_entity AS
SELECT
  entity_id       AS 实体ID,
  canonical_title AS 实体,
  category        AS 分类标签,
  agg_peak        AS 聚合热度_峰值式,
  agg_joint       AS 聚合热度_联合式,
  platform_count  AS 覆盖平台数,
  platforms       AS 覆盖平台,
  max_norm_score  AS 单平台峰值,
  spread_bonus    AS 扩散加成,
  best_position   AS 最佳名次,
  first_seen_at   AS 上榜时间,
  trend_flag      AS 趋势标记
FROM entity_metric;

-- =============================================================
-- 事件首发溯源（只对"值得追"的触发事件做，不做全量帖子级采集）
-- =============================================================
-- 事件跟踪注册表：一行 = 一个被触发的事件（沿用 cluster_id 作主键，天然去重）。
-- tier 决定优先级：L1=跨平台共振/榜首/名次跃升（优先溯源）；L2=TOP10 持续（次优先）。
-- status：pending=已触发待溯源 → active=已产出首发信号；paused/ended=人工控制。
CREATE TABLE IF NOT EXISTS event_track (
  cluster_id     INTEGER PRIMARY KEY,
  title          TEXT,                       -- 代表标题（簇 label）
  tier           TEXT NOT NULL DEFAULT 'L2' CHECK (tier IN ('L1','L2')),
  status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','active','paused','ended')),
  trigger_reason TEXT,                       -- 触发规则标识
  platform_count INTEGER,
  platforms      TEXT,
  peak_hot       INTEGER,
  best_position  INTEGER,
  coverage       TEXT,                       -- 计划覆盖的搜索源（JSON 数组）
  trace_count    INTEGER NOT NULL DEFAULT 0,
  triggered_at   TEXT NOT NULL,
  last_traced_at TEXT,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_status ON event_track (status);
CREATE INDEX IF NOT EXISTS idx_event_tier   ON event_track (tier);

-- 首发信号：一行 = 一个事件的"已覆盖源中最早"发声点（人工补链会覆盖 is_manual=1）。
-- 注意：不声称"全网首发"，只保证"已覆盖源中最早"，并记录覆盖源与置信度。
CREATE TABLE IF NOT EXISTS event_earliest_signal (
  cluster_id      INTEGER PRIMARY KEY,
  url             TEXT,
  site            TEXT,
  posted_at       TEXT,                      -- 发布时刻（文章自带时间优先）
  title           TEXT,
  content_excerpt TEXT,                      -- 正文摘要（用于判断"是否报道了同一件事"）
  source_coverage TEXT,                      -- 覆盖源清单（JSON 数组）
  confidence      REAL,
  is_manual       INTEGER NOT NULL DEFAULT 0,
  searched_at     TEXT NOT NULL,
  snapshot_path   TEXT                       -- 原始 HTML 快照本地路径（data/snapshots/...）
);

-- 事件跟踪出口：注册表 + 首发信号左连接，供看板直接用
DROP VIEW IF EXISTS v_event_track;
CREATE VIEW v_event_track AS
SELECT
  t.cluster_id, t.title, t.tier, t.status, t.trigger_reason,
  t.platform_count, t.platforms, t.peak_hot, t.best_position,
  t.coverage, t.trace_count, t.triggered_at, t.last_traced_at, t.updated_at,
  s.url AS earliest_url, s.site AS earliest_site, s.posted_at AS earliest_posted_at,
  s.title AS earliest_title, s.content_excerpt, s.source_coverage,
  s.confidence, s.is_manual, s.searched_at, s.snapshot_path
FROM event_track t
LEFT JOIN event_earliest_signal s ON s.cluster_id = t.cluster_id;
