FROM python:3.10-slim

# Устанавливаем системные зависимости включая FFmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Создаем и переходим в рабочую директорию
WORKDIR /app

# Копируем requirements и устанавливаем Python зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы
COPY . .

# Создаем папку для данных
RUN mkdir -p /app/data

# Запускаем приложение
CMD ["python", "bot.py"]