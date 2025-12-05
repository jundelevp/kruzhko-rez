FROM python:3.11-slim

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY bot.py .

# Создаем временную директорию
RUN mkdir -p /tmp/video_temp && chmod 777 /tmp/video_temp

# Используем непривилегированного пользователя
USER 1000

# Запускаем бота
CMD ["python", "bot.py"]
