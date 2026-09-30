# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# ImageMagick with the libwmf delegate converts WMF/EMF figures to PNG during synchronisation
RUN apt-get update \
 && apt-get install -y --no-install-recommends imagemagick libmagickcore-6.q16-6-extra tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 hsa

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
# Optional offline wheelhouse (deploy/build.sh fills backend/wheels/ from the host pip cache on
# slow networks); without wheels the dependencies come from PyPI.
COPY wheels /wheels
RUN if ls /wheels/*.whl >/dev/null 2>&1; then pip install --no-index --find-links=/wheels .; else pip install .; fi \
 && rm -rf /wheels /root/.cache
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && mkdir -p /data/media && chown -R hsa:hsa /data

USER hsa
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--", "docker-entrypoint.sh"]
CMD ["api"]
