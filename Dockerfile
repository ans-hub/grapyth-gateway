FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GRAPYTH_GATEWAY_DATA_ROOT=/var/lib/grapyth-gateway

WORKDIR /opt/grapyth
RUN addgroup --system grapyth-gateway && adduser --system --ingroup grapyth-gateway grapyth-gateway \
    && mkdir -p /var/lib/grapyth-gateway && chown grapyth-gateway:grapyth-gateway /var/lib/grapyth-gateway

COPY requirements.txt ./gateway/requirements.txt
RUN pip install --no-cache-dir -r gateway/requirements.txt

COPY . ./gateway
RUN chmod -R u=rwX,go=rX /opt/grapyth/gateway

USER grapyth-gateway
EXPOSE 8077
VOLUME ["/var/lib/grapyth-gateway"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8077/health', timeout=2)" || exit 1

CMD ["uvicorn", "gateway.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8077", "--no-access-log"]
