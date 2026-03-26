FROM python:3.11-slim

WORKDIR /app

# git is required for cloning target repos
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Usage: docker run <image> owner/repo [--all | --unassigned | ISSUE_NUMBER...]
ENTRYPOINT ["python", "run.py"]
