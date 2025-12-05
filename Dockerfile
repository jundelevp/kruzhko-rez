FROM python:3.10-slim

# Меняем зеркало для ускорения загрузки
RUN sed -i 's|deb.debian.org|mirror.yandex.ru|g' /etc/apt/sources.list && \
    sed -i 's|security.debian.org|mirror.yandex.ru/debian-security|g' /etc/apt/sources.list

# Устанавливаем FFmpeg и системные зависимости
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1-mesa-glx \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY . .

# Создаем директорию для временных файлов
RUN mkdir -p /app/video_temp

# Запуск бота
CMD ["python", "main.py"]