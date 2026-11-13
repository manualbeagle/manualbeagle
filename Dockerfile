# Manual Beagle's web UI, HTTP API and CLI in one image, with OCR (Tesseract and OCRmyPDF) built in.
# How to run it: docs/docker.md. Build it yourself with `docker build -t manualbeagle .`
#
# About 250 MB unpacked: the slim Python image, Tesseract (about 50 MB) and the Python packages (about 110 MB,
# most of it OCRmyPDF's). A second build stage was tried and saved nothing: every dependency installs from a
# wheel, so no compiler or build files are left behind. The base image and the Debian packages exist for
# linux/amd64 and linux/arm64, so `docker buildx build --platform linux/amd64,linux/arm64 .` builds both (the
# release workflow does).

FROM python:3.15-slim

ARG VERSION=dev
LABEL org.opencontainers.image.title="Manual Beagle" \
      org.opencontainers.image.description="Finds appliance manuals by brand and model, makes them searchable per page, and files them in Homebox and other inventories." \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/manualbeagle/manualbeagle" \
      org.opencontainers.image.documentation="https://github.com/manualbeagle/manualbeagle/blob/main/docs/docker.md"

# Tesseract for OCR and rating plates. Ghostscript isn't needed: OCR writes plain PDFs (see library.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir --disable-pip-version-check ".[web,mcp]" "ocrmypdf>=17" \
    && cd / && rm -rf /build

# Run as an ordinary user. /data holds the library, its database and the web UI's saved settings; /config may
# hold a config.toml (without one, every setting has its default).
RUN useradd --create-home --uid 1000 beagle \
    && mkdir -p /data /config \
    && chown beagle:beagle /data
USER beagle
WORKDIR /data
ENV MANUALBEAGLE_HOME=/data \
    MANUALBEAGLE_CONFIG=/config/config.toml \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4)"

# Listening on every address of the container means a token is always required: set MANUALBEAGLE_WEB_TOKEN,
# or the server makes one and prints a link that carries it.
CMD ["manualbeagle", "serve", "--web", "--host", "0.0.0.0", "--port", "8765"]
