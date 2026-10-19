FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /ml
COPY ml_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ml_service /ml
COPY data /data
RUN mkdir -p /ml/artifacts
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8001"]
