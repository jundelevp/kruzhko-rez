FROM python:3.10-slim

# Timeweb: меняем зеркало для ускорения
RUN sed -i 's|deb.debian.org|mirror.yandex.ru|g' /etc/apt/sources.list && \
    sed -i 's|security.debian.org|mirror.yandex.ru/debian-security|g' /etc/apt/sources.list

# Timeweb: устанавливаем FFmpeg и зависимости
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1-mesa-glx \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

# Timeweb: настройка окружения Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    UV_THREADPOOL_SIZE=2

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY bot.py .
COPY .env .  # Если есть .env файл

# Создаем временную директорию
RUN mkdir -p /tmp/video_bot && chmod 777 /tmp/video_bot

# Timeweb: запуск с оптимизациями
CMD ["python", "-OO", "bot.py"]
