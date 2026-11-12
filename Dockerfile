FROM python:3.12-slim
WORKDIR /srv
RUN pip install --no-cache-dir fastapi==0.120.4 starlette==0.49.3 "uvicorn[standard]==0.34.0" "psycopg[binary]==3.2.3" pywebpush==2.5.0
COPY app/ ./app/
COPY agent/dist/ ./agente/
RUN useradd -r -u 10001 app && mkdir -p /srv/clipes && chown app /srv/clipes
USER app
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
