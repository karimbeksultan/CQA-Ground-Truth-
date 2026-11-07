# CQA demo server: FastAPI + the web UI. Secrets (CQA_API_URL, GEMINI_API_KEY, CQA_BASIC_AUTH,
# CQA_TEAM_NAME) come from the platform's environment settings, never from the image.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY cqa ./cqa
COPY web ./web
COPY checklists ./checklists
COPY results ./results
COPY eval ./eval
# On boot: import DEV from the organizer API and show the committed DEV results (idempotent), then serve.
CMD ["sh", "-c", "python -m cqa seed-demo --group DEV --results results/dev/results.json || echo 'seed skipped'; exec uvicorn cqa.api:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
