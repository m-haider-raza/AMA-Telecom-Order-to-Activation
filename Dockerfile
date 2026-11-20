FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# The Azure SQL driver loads Microsoft's ODBC libraries, which need these system packages.
RUN apt-get update && apt-get install -y --no-install-recommends libltdl7 libkrb5-3 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY pyproject.toml .
COPY app ./app
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 demo \
    && mkdir -p /srv/data && chown demo:demo /srv/data
USER demo
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
