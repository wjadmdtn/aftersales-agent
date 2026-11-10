# 多阶段：前端构建 → 后端镜像挂静态产物
# 后端不依赖前端即可单独构建（见 README），这里只服务"一次出可演示镜像"

FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# 构建失败即镜像失败——这是 FR-021 在没有 CI 时唯一的自动校验点
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir fastapi uvicorn websockets pydantic pydantic-settings redis httpx

COPY backend/app ./app
COPY backend/seed ./seed

# 前端产物挂到 app/static，create_app 检测到才挂载
COPY --from=frontend /fe/dist ./app/static

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
