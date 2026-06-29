FROM python:3.12-slim

WORKDIR /bot

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV BOT_TOKEN=""
ENV ADMIN_ID=""
ENV AI_PROVIDER="local"
ENV PROXY_URL=""

EXPOSE 5000
CMD ["python", "runner.py"]
