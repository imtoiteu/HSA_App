FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# ImageMagick (libwmf delegate) converts WMF/EMF figures that upstream has not pre-rendered.
# It is only a fallback (upstream's rendered/cache/vec PNGs are used first); set
# WITH_IMAGEMAGICK=0 to build without apt access. Missing ImageMagick never produces broken
# content: an unconvertible figure marks its question NEEDS_VISUAL_REVIEW.
ARG WITH_IMAGEMAGICK=1
RUN if [ "$WITH_IMAGEMAGICK" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends imagemagick libmagickcore-6.q16-6-extra \
      && rm -rf /var/lib/apt/lists/*; fi \
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
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["api"]
