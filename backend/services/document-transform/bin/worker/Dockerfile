FROM python:3.11.18-slim-trixie@sha256:82209229d3fda30d71e603ec95bb4bea0476635ba6df3df2e32284e036654f51

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libssl-dev \
    libffi-dev \
    ca-certificates \
    default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Catch regressions in escaped OpenDataLoader page separators at image-build time.
RUN python -m unittest discover -s tests -v

CMD ["python", "main.py"]
