FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn modelshare.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
