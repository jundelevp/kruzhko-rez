FROM python:3.10-slim

# Меняем зеркало на Yandex для Timeweb (оптимизированная связь с РФ)
RUN sed -i 's|deb.debian.org|mirror.yandex.ru|g' /etc/apt/sources.list && \
    sed -i 's|security.debian.org|mirror.yandex.ru/debian-security|g' /etc/apt/sources.list

# Оптимизация для малой памяти (2 ГБ) - устанавливаем только самое необходимое
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    # Минимальный ffmpeg для бота (без GUI, X11, документации)
    ffmpeg \
    # Добавляем дополнительные библиотеки для обработки медиа
    libopus0 \
    libvpx9 \
    # Утилиты для работы с файлами
    procps \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Устанавливаем рабочий каталог
WORKDIR /app

# Копируем только requirements.txt сначала (для кэширования слоя pip)
COPY requirements.txt .

# Оптимизация pip для малой памяти
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Устанавливаем зависимости с оптимизацией
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем остальной код
COPY . .

# Ограничения памяти для Python (адаптировано под 2 ГБ ОЗУ)
ENV PYTHONGC=2 \
    OMP_NUM_THREADS=2 \
    MALLOC_ARENA_MAX=2

# Создаем непривилегированного пользователя для безопасности (важно для Timeweb)
RUN useradd -m -u 1000 -s /bin/bash appuser && \
    chown -R appuser:appuser /app

USER appuser

# Команда запуска с оптимизацией памяти
CMD ["python", "-OO", "-W", "ignore", "bot.py"]
