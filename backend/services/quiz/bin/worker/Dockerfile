# ================= BASE =================
FROM python:3.11-slim

# ================= ENV =================
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ================= SYSTEM DEPENDENCIES =================
# Pulsar client necesita librerías nativas
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    libffi-dev \
    libcurl4-openssl-dev \
    libprotobuf-dev \
    protobuf-compiler \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ================= WORKDIR =================
WORKDIR /app

# ================= PYTHON DEPENDENCIES =================
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# ================= APP =================
COPY . .

# ================= SECURITY =================
# no correr como root
RUN useradd -m worker
# El usuario worker necesita escritura en /app (tmp/, output/)
RUN chown -R worker:worker /app
USER worker

# ================= HEALTHCHECK =================
# opcional pero recomendado en k8s
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python healthcheck.py || exit 1

# ================= ENTRYPOINT =================
CMD ["python", "main.py"]