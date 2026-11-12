# syntax=docker/dockerfile:1

# ---- 阶段 1：前端构建 ----
# 前端构建失败 → 镜像构建失败。这是 FR-021 唯一的自动校验点
# （项目刻意不做 CI，见 README「演示级限制」）。
FROM node:22-slim AS frontend

WORKDIR /web
# 先只拷依赖清单：源码改动不会让 npm ci 这层缓存失效
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# build 脚本 = vue-tsc --noEmit && vite build，类型错误同样会阻断
RUN npm run build


# ---- 阶段 2：后端运行时 ----
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# 按 pyproject 装运行期依赖，不手抄包名——手抄会与 pyproject 逐渐分叉
# （此前漏了 websockets，/ws/* 全部 404）。
# [serve] 提供 fakeredis：镜像内不带 Redis，由 dev_server 顶替（见 CMD）。
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir ".[serve]"

COPY backend/app ./app
COPY backend/seed ./seed

# 前端产物由后端托管（frontend_static_dir 默认 app/static）
COPY --from=frontend /web/dist ./app/static

EXPOSE 8000

# 生产入口用 dev_server：镜像内不带 Redis，由 fakeredis 顶替。
# 演示级项目的取舍，已在 README「演示级限制」写明。
CMD ["uvicorn", "dev_server:app", "--host", "0.0.0.0", "--port", "8000"]
