FROM python:3.13-alpine
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY asentum_exporter.py /app/asentum_exporter.py
USER 65534:65534
EXPOSE 7980
ENTRYPOINT ["python3", "/app/asentum_exporter.py"]
