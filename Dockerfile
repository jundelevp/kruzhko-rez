FROM python:3.10-slim

# Устанавливаем только FFmpeg
RUN apt-get update && apt-get install -y ffmpeg

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY bot.py .

# Открываем порт для health check (Timeweb требует!)
EXPOSE 8080

CMD ["python", "bot.py"]
