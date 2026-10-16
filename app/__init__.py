"""热点聚合 MVP · 免费接口采集 + 历史留痕。

包结构：
  schema.sql   历史留痕数据库表结构
  db.py        SQLite 连接/初始化/写入
  normalize.py 标题归一化
  adapters.py  7 个免费平台适配器
  runner.py    采集调度入口（CLI）
"""

__version__ = "0.1.0"
