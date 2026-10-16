FROM python:3.12-slim

WORKDIR /app

# 只复制代码与配置；数据目录（SQLite/快照/简报）不打包进镜像，
# 运行时以 volume 挂载（docker-compose 已配好 ./data:/app/data）。
COPY app/ ./app/
COPY config/ ./config/
COPY requirements.txt .
COPY README.md .

# 预创建运行时目录（volume 挂载后自动生效）
RUN mkdir -p /app/data/briefs /app/data/snapshots

ENV PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1

# 看板服务；采集调度由 docker-compose 的 scheduler 服务单独启动。
# 显式监听 0.0.0.0：未配置 ADMIN_TOKEN 时非本机写接口一律 403（只读安全），
# 配置 ADMIN_TOKEN 后远程管理需携带 Authorization: Bearer <token>。
EXPOSE 8000
CMD ["python", "-m", "app.web", "--host", "0.0.0.0", "--port", "8000"]
