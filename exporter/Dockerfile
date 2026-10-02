FROM python:3.13-alpine
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY mersennet_exporter.py /app/mersennet_exporter.py
USER 65534:65534
EXPOSE 7979
ENTRYPOINT ["python3", "/app/mersennet_exporter.py"]
