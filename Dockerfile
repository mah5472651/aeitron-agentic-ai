FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements-local-dev.txt /app/requirements-local-dev.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements-local-dev.txt

COPY . /app
EXPOSE 8090
CMD ["python", "-m", "uvicorn", "src.mythos.gateway.api:app", "--host", "0.0.0.0", "--port", "8090"]
