FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY checker ./checker
COPY sites.example.csv ./sites.example.csv
COPY config.yaml ./config.yaml
RUN useradd --uid 10001 --create-home checker
USER 10001:10001
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 checker.web:app"]
