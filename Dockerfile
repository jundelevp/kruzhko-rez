FROM python:3.10-slim

# 1. Сначала проверяем/создаем sources.list
RUN test -f /etc/apt/sources.list || echo "deb http://deb.debian.org/debian trixie main" > /etc/apt/sources.list && \
    echo "deb http://deb.debian.org/debian trixie-updates main" >> /etc/apt/sources.list && \
    echo "deb http://security.debian.org/debian-security trixie-security main" >> /etc/apt/sources.list

# 2. Обновляем и устанавливаем пакеты
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1-mesa-glx \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY bot.py .

# Создаем временную директорию
RUN mkdir -p /tmp/video_bot && chmod 777 /tmp/video_bot

CMD ["python", "-OO", "bot.py"]
