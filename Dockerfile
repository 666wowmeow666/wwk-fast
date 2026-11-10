FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
COPY . /src
RUN f="$(find /src -name alembic.ini | head -1)" && test -n "$f" && d="$(dirname "$f")" && mkdir -p /app && cp -a "$d"/. /app/ && rm -rf /src
WORKDIR /app
RUN pip install -r requirements.txt
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app
EXPOSE 8080
CMD ["python", "-m", "wwk.main"]
