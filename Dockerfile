FROM python:3.10-slim

# 1. Сначала запускаем apt-get update, чтобы создать sources.list
RUN apt-get update

# 2. ТЕПЕРЬ меняем зеркало (файл уже существует)
RUN sed -i 's|deb.debian.org|mirror.yandex.ru|g' /etc/apt/sources.list && \
    sed -i 's|security.debian.org|mirror.yandex.ru/debian-security|g' /etc/apt/sources.list

# 3. Обновляем репозитории с новым зеркалом
RUN apt-get update

# 4. Устанавливаем FFmpeg и зависимости
RUN apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1-mesa-glx \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Оптимизация Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY bot.py .

# Создаем временную директорию
RUN mkdir -p /tmp/video_bot && chmod 777 /tmp/video_bot

# Запуск бота
CMD ["python", "-OO", "bot.py"]

