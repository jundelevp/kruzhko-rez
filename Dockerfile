FROM python:3.10-slim

# 1. Сначала обновляем APT и устанавливаем нужные пакеты
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

# 2. Меняем зеркало репозитория (после установки основных пакетов)
# Для Debian bullseye (который используется в python:3.10-slim)
RUN echo "deb http://mirror.yandex.ru/debian bullseye main" > /etc/apt/sources.list && \
    echo "deb http://mirror.yandex.ru/debian-security bullseye-security main" >> /etc/apt/sources.list && \
    echo "deb http://mirror.yandex.ru/debian bullseye-updates main" >> /etc/apt/sources.list

WORKDIR /app

# Оптимизация Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
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
