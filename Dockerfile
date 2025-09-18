FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

# Default: run the web app. The migration Job overrides the command.
CMD ["uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
