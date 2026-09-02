FROM python:3.12-slim

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    libjpeg62-turbo \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*
# fonts-dejavu-core: PIL default-font fallback if a custom font ever fails to load
# libjpeg/zlib: Pillow image decode/encode support

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
