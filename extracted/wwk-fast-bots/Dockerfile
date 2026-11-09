FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY wwk ./wwk

RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8080
CMD ["python", "-m", "wwk.main"]
