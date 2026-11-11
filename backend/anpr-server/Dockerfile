FROM python:3.11-slim

# System deps for opencv
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only PyTorch first (prevents CUDA wheels)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch==2.15.0 torchvision==0.29.2 --index-url https://download.pytorch.org/whl/cpu

# Now install the rest
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the EasyOCR weights (~80 MB) into the image so a cold container
# doesn't download them at startup
RUN python -c "import easyocr; easyocr.Reader(['en'], gpu=False)"

# Copy your server code
COPY . .

# Cloud Run sets $PORT (8080); locally it defaults to 8000
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
