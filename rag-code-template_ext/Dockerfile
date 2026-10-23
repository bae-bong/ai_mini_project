FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements-e.txt ./
RUN pip install --no-cache-dir -r requirements-e.txt
COPY . .
EXPOSE 8001
CMD ["uvicorn", "rag_external-web_pdf_closed_llm:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
