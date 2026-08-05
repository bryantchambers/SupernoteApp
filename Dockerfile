# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm

ARG TARGETARCH
ARG CALIBRE_VERSION=9.11.0
ARG RCLONE_RELEASE=1.74.4

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/calibre:/home/app/.local/bin:${PATH}" \
    QT_QPA_PLATFORM=offscreen \
    XDG_CACHE_HOME=/tmp/.cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        libdbus-1-3 \
        libegl1 \
        libfontconfig1 \
        libgl1 \
        libxkbcommon0 \
        pkg-config \
        sqlite3 \
        unzip \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN case "${TARGETARCH}" in \
        amd64) binary_arch="amd64"; calibre_arch="x86_64" ;; \
        arm64) binary_arch="arm64"; calibre_arch="arm64" ;; \
        *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && rclone_file="rclone-v${RCLONE_RELEASE}-linux-${binary_arch}.zip" \
    && curl -fsSL "https://downloads.rclone.org/v${RCLONE_RELEASE}/${rclone_file}" -o "/tmp/${rclone_file}" \
    && curl -fsSL "https://downloads.rclone.org/v${RCLONE_RELEASE}/SHA256SUMS" -o /tmp/SHA256SUMS \
    && cd /tmp \
    && grep " ${rclone_file}$" SHA256SUMS | sha256sum -c - \
    && unzip -q "${rclone_file}" \
    && install -m 0755 "rclone-v${RCLONE_RELEASE}-linux-${binary_arch}/rclone" /usr/local/bin/rclone \
    && rm -rf "/tmp/${rclone_file}" /tmp/SHA256SUMS "/tmp/rclone-v${RCLONE_RELEASE}-linux-${binary_arch}" \
    && curl -fsSL "https://download.calibre-ebook.com/${CALIBRE_VERSION}/calibre-${CALIBRE_VERSION}-${calibre_arch}.txz" -o /tmp/calibre.txz \
    && mkdir -p /opt/calibre \
    && tar -xJf /tmp/calibre.txz -C /opt/calibre \
    && rm /tmp/calibre.txz \
    && rclone version \
    && ebook-convert --version

WORKDIR /app

COPY requirements-container.txt .
RUN python -m pip install --no-cache-dir -r requirements-container.txt

COPY . .
RUN python manage.py check && supernote-tool --version
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data /config /backups /static \
    && chown -R app:app /data /config /backups /static /home/app \
    && chmod +x /app/deploy/entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["/bin/sh", "/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "supernote_project.wsgi:application", "--bind=0.0.0.0:8000", "--workers=1", "--threads=4", "--timeout=1000", "--access-logfile=-", "--error-logfile=-"]
