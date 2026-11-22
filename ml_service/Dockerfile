FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/ml/hf_cache
WORKDIR /ml
# PyTorch solo CPU (evita descargar CUDA, varios GB).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY ml_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# El modelo de embeddings (~470 MB) queda dentro de la imagen: el contenedor no necesita internet al arrancar.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
COPY ml_service /ml
COPY data /data
RUN mkdir -p /ml/artifacts
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8001"]
