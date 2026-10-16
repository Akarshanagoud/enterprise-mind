FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd -r -u 10001 emind && mkdir -p /var/emind && chown emind /var/emind
COPY data ./data
USER emind
ENV EMIND_DATA_DIR=/var/emind EMIND_DOCS_DIR=/app/data/sample_docs
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health')"
CMD ["python", "-m", "emind", "serve", "--host", "0.0.0.0", "--port", "8000"]
