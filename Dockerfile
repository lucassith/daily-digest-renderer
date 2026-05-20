FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV MPLBACKEND=Agg

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/ /app/

EXPOSE 8099

CMD ["python", "-m", "daily_digest_renderer"]
