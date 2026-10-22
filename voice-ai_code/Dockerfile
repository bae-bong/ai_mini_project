FROM python:3.11-slim

# System packages
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependencies first to leverage cache
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Application source
COPY . /app

# (Optional) It is recommended to remove --reload in production
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8008","--reload"]
