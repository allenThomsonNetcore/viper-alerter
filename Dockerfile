FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY checker ./checker
COPY sites.example.csv ./sites.example.csv
RUN useradd --uid 10001 --create-home checker
USER 10001:10001
CMD ["python", "-m", "checker.main"]
