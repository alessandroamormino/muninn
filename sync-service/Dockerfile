FROM python:3.11-slim

# Set working directory inside container
WORKDIR /app

# Install dependencies first (separate layer for Docker cache efficiency)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m spacy download it_core_news_sm

# Copy application source
COPY . .

# Expose FastAPI port
EXPOSE 8000

# Run uvicorn; reload is disabled in production but enabled here for dev convenience
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
