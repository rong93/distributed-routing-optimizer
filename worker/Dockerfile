FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install procps to support top command for resource telemetry reporting
RUN apt-get update && apt-get install -y procps && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 4000

CMD ["python", "worker.py"]
