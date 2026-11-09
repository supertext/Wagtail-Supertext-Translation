# Supertext Wagtail demo: Wagtail 8 + wagtail-localize + this package, on PostgreSQL.
# Build context is the repository root:
#   docker build -f demo/Dockerfile -t supertext-wagtail-demo .
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY demo/requirements.txt demo/requirements.txt
RUN pip install -r demo/requirements.txt

# The package itself, from this repository
COPY pyproject.toml README.md LICENSE ./
COPY wagtail_supertext wagtail_supertext
RUN pip install .

COPY demo demo
RUN cd demo && DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput -v 0 \
 && chmod +x entrypoint.sh

EXPOSE 8000
CMD ["/app/demo/entrypoint.sh"]
