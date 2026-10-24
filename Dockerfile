FROM python:3.11-slim

# 프론트엔드 빌드와 R2 스냅샷에 필요한 도구
RUN apt-get update \
  && apt-get install -y --no-install-recommends nodejs npm rclone \
  && rm -rf /var/lib/apt/lists/*

# 从 uv 官方镜像复制 uv
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# 先复制依赖描述文件以利用缓存
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY backend/pyproject.toml backend/uv.lock ./backend/

# 安装依赖（Node + Python）
RUN npm ci \
  && npm ci --prefix frontend \
  && cd backend && uv sync --frozen

# 复制项目源码
COPY . .

# 화면을 빌드하면 Flask가 frontend/dist를 함께 서빙한다.
RUN npm run build --prefix frontend \
  && chmod +x /app/deploy/cloudflare/entrypoint.sh

ENV FLASK_HOST=0.0.0.0 \
    FLASK_PORT=8080 \
    SENTIMENT_MODE=llm \
    PYTHONUNBUFFERED=1 \
    PATH="/app/backend/.venv/bin:${PATH}"

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

ENTRYPOINT ["/app/deploy/cloudflare/entrypoint.sh"]