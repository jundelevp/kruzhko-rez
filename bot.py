#!/usr/bin/env python3
"""
Video Circle to Reels Converter Bot
ОПТИМИЗИРОВАН ДЛЯ TIMEWEB (2 ГБ ОЗУ, 3.3 ГГц)
"""

import os
import sys
import logging
import gc
import asyncio
import resource
from pathlib import Path
from typing import Optional
import subprocess

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
import moviepy.editor as mp
from moviepy.video.VideoClip import ColorClip
import psutil

# --- TIMEWEB КОНФИГУРАЦИЯ ---
BOT_TOKEN = "8535285877:AAFkJEwV18KFCnEJPAyTR2AsSsgvQbTA6fg"

# 1. ПАМЯТЬ (2 ГБ ОЗУ)
MEMORY_LIMIT_MB = 1800  # Оставляем 200 МБ для системы
MAX_VIDEO_SIZE = 40 * 1024 * 1024  # 40 MB
MAX_VIDEO_DURATION = 45  # 45 секунд максимум
MAX_CACHE_SIZE_MB = 100  # Макс. размер кэша

# 2. CPU (3.3 ГГц, 1-2 ядра на Timeweb)
FFMPEG_THREADS = 2  # Оптимально для 2 ядер

# 3. КАЧЕСТВО ВЫХОДНОГО ВИДЕО (оптимизировано)
OUTPUT_WIDTH = 720   # HD (быстрее чем 1080)
OUTPUT_HEIGHT = 1280 # Вертикальное 9:16
OUTPUT_FPS = 30
OUTPUT_PRESET = 'ultrafast'  # Самый быстрый пресет
OUTPUT_CRF = 23  # Хороший баланс качество/размер
OUTPUT_BITRATE = '2M'  # Умеренный битрейт

# 4. ПУТИ И ДИРЕКТОРИИ
TEMP_DIR = Path("/tmp/video_bot")  # Используем /tmp (быстрее на Timeweb)
TEMP_DIR.mkdir(exist_ok=True, parents=True)

# 5. ОПТИМИЗАЦИЯ ПАМЯТИ PYTHON
gc.enable()
gc.set_threshold(500, 5, 5)  # Агрессивный сбор мусора

# Ограничение памяти процесса (только для Linux)
if sys.platform == "linux":
    try:
        # Устанавливаем мягкий лимит в 1.8 ГБ
        soft_limit = MEMORY_LIMIT_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (soft_limit, resource.RLIM_INFINITY))
    except:
        pass

# --- ЛОГГИРОВАНИЕ (оптимизировано) ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(
    token=BOT_TOKEN,
    parse_mode=ParseMode.HTML
)
dp = Dispatcher()

# Семафор для ограничения одновременных конвертаций
conversion_semaphore = asyncio.Semaphore(1)  # Только 1 одновременно

# --- ОПТИМИЗИРОВАННЫЕ ФУНКЦИИ ДЛЯ TIMEWEB ---

def check_memory_usage() -> bool:
    """Проверяет, достаточно ли памяти для обработки"""
    process = psutil.Process()
    memory = process.memory_info().rss / 1024 / 1024  # MB
    
    if memory > MEMORY_LIMIT_MB * 0.8:  # Если используется >80%
        logger.warning(f"Мало памяти: {memory:.1f} MB из {MEMORY_LIMIT_MB} MB")
        return False
    return True

async def cleanup_temp_files():
    """Умная очистка временных файлов"""
    try:
        deleted = 0
        total_size = 0
        
        # Получаем список файлов отсортированный по времени
        files = sorted(TEMP_DIR.glob("*"), key=lambda x: x.stat().st_mtime)
        
        # Удаляем старые файлы если кэш больше лимита
        cache_size_mb = sum(f.stat().st_size for f in files) / 1024 / 1024
        
        if cache_size_mb > MAX_CACHE_SIZE_MB:
            # Удаляем 50% самых старых файлов
            files_to_delete = files[:len(files)//2]
            for file in files_to_delete:
                try:
                    total_size += file.stat().st_size
                    file.unlink()
                    deleted += 1
                except:
                    continue
            
            logger.info(f"Очищен кэш: {deleted} файлов, {total_size/1024/1024:.1f} MB")
        
        return deleted
        
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")
        return 0

def create_reels_video_timeweb(input_path: Path, user_id: int) -> Optional[Path]:
    """
    Оптимизированная конвертация для Timeweb
    """
    output_path = TEMP_DIR / f"reels_{user_id}.mp4"
    
    try:
        if not check_memory_usage():
            logger.error("Недостаточно памяти для обработки")
            return None
        
        logger.info(f"Конвертация: {input_path.name}")
        
        # 1. Загружаем видео с оптимизацией
        video = mp.VideoFileClip(
            str(input_path),
            audio=True,
            target_resolution=(480, 480)  # Загружаем в меньшем разрешении
        )
        
        # 2. Проверяем длительность
        if video.duration > MAX_VIDEO_DURATION:
            logger.warning(f"Слишком длинное видео: {video.duration:.1f}с")
            video.close()
            return None
        
        # 3. Оптимизированная обработка для Timeweb
        # Уменьшаем разрешение если нужно
        target_height = OUTPUT_HEIGHT
        original_size = video.size
        circle_size = min(original_size)
        
        # Масштабируем
        scale_factor = target_height / circle_size
        if scale_factor > 3:  # Если увеличение слишком большое
            scale_factor = 2  # Ограничиваем
        
        # Создаем черный фон
        background = ColorClip(
            size=(OUTPUT_WIDTH, OUTPUT_HEIGHT),
            color=(0, 0, 0),
            duration=video.duration
        ).set_fps(OUTPUT_FPS)
        
        # Подготовка круга
        x_center = original_size[0] // 2
        y_center = original_size[1] // 2
        half_size = circle_size // 2
        
        cropped = video.crop(
            x1=x_center - half_size,
            y1=y_center - half_size,
            x2=x_center + half_size,
            y2=y_center + half_size
        )
        
        scaled = cropped.resize(scale_factor)
        
        # Позиционируем по центру
        x_pos = (OUTPUT_WIDTH - scaled.w) // 2
        y_pos = (OUTPUT_HEIGHT - scaled.h) // 2
        
        # 4. Композиция с оптимизацией памяти
        final_video = mp.CompositeVideoClip(
            [background, scaled.set_position((x_pos, y_pos))],
            size=(OUTPUT_WIDTH, OUTPUT_HEIGHT),
            use_bgclip=True
        ).set_duration(video.duration)
        
        if video.audio:
            final_video = final_video.set_audio(video.audio)
        
        # 5. Экспорт с Timeweb оптимизациями
        logger.info("Экспорт видео...")
        
        # Параметры FFmpeg для Timeweb
        ffmpeg_params = [
            '-threads', str(FFMPEG_THREADS),
            '-preset', OUTPUT_PRESET,
            '-crf', str(OUTPUT_CRF),
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            '-max_muxing_queue_size', '9999',
            '-bufsize', '2000k'  # Буфер для стабильности
        ]
        
        final_video.write_videofile(
            str(output_path),
            codec='libx264',
            audio_codec='aac',
            audio_bitrate='128k',
            temp_audiofile=str(TEMP_DIR / f"audio_{user_id}.m4a"),
            remove_temp=True,
            threads=FFMPEG_THREADS,
            ffmpeg_params=ffmpeg_params,
            verbose=False,
            logger=None  # Отключаем логирование moviepy
        )
        
        # 6. Очистка памяти
        video.close()
        cropped.close()
        scaled.close()
        background.close()
        final_video.close()
        
        # Принудительный сбор мусора
        gc.collect()
        
        # 7. Проверка результата
        if not output_path.exists():
            logger.error("Выходной файл не создан")
            return None
        
        file_size = output_path.stat().st_size
        if file_size > MAX_VIDEO_SIZE:
            logger.warning(f"Файл слишком большой: {file_size/1024/1024:.1f} MB")
            output_path.unlink()
            return None
        
        logger.info(f"✅ Готово: {output_path.name} ({file_size/1024/1024:.1f} MB)")
        return output_path
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        # Закрываем все клипы при ошибке
        try:
            video.close()
        except:
            pass
        return None

# --- КОМАНДЫ БОТА ---

@dp.message(Command("start", "help"))
async def cmd_start(message: Message):
    start_text = """
🎬 <b>Video Circle → Reels Converter</b>
<i>Оптимизирован для Timeweb сервера</i>

<b>🚀 Что я делаю:</b>
• Беру ваш видео-кружок (видео-заметку)
• Увеличиваю его с сохранением качества
• Добавляю стильный черный фон
• Создаю вертикальное видео 720×1280
• Сохраняю оригинальный звук

<b>📊 Ограничения сервера:</b>
• Макс. длительность: 45 секунд
• Макс. размер: 40 MB
• Обработка по очереди (по одному)

<b>📌 Как использовать:</b>
Просто отправьте мне <b>видео-кружок</b> (видео заметку)

<b>⚙️ Команды:</b>
/status - статус сервера
/cleanup - очистка кэша
"""
    await message.answer(start_text)

@dp.message(Command("status"))
async def cmd_status(message: Message):
    """Статус сервера Timeweb"""
    process = psutil.Process()
    memory = process.memory_info()
    
    # Использование памяти
    memory_used = memory.rss / 1024 / 1024
    memory_percent = (memory_used / MEMORY_LIMIT_MB) * 100
    
    # CPU
    cpu_percent = process.cpu_percent(interval=0.5)
    
    # Диск
    disk = psutil.disk_usage('/')
    
    status_text = f"""
🖥 <b>Статус Timeweb сервера:</b>

<b>Память (RAM):</b>
• Используется: {memory_used:.1f} MB / {MEMORY_LIMIT_MB} MB
• Загрузка: {memory_percent:.1f}%

<b>Процессор (CPU):</b>
• Загрузка: {cpu_percent:.1f}%
• Потоки FFmpeg: {FFMPEG_THREADS}

<b>Дисковое пространство:</b>
• Свободно: {disk.free / 1024 / 1024 / 1024:.1f} GB
• Занято: {disk.percent}%

<b>Очередь обработки:</b>
• Активных задач: {conversion_semaphore._value}
• Файлов в кэше: {len(list(TEMP_DIR.glob('*')))}
"""
    
    await message.answer(status_text)

@dp.message(Command("cleanup"))
async def cmd_cleanup(message: Message):
    """Очистка кэша"""
    deleted = await cleanup_temp_files()
    await message.answer(f"✅ Очищено {deleted} временных файлов")

# --- ОБРАБОТКА ВИДЕО-КРУЖКОВ ---

@dp.message(F.video_note)
async def handle_video_note(message: Message):
    """Обработка видео-кружков с ограничением"""
    async with conversion_semaphore:
        user_id = message.from_user.id
        
        # Проверяем память перед началом
        if not check_memory_usage():
            await message.answer("⚠️ <b>Сервер перегружен</b>\n"
                                "Попробуйте через несколько минут")
            return
        
        status_msg = await message.answer("🔄 <b>Начинаю обработку...</b>")
        
        try:
            # Скачивание
            file_id = message.video_note.file_id
            file = await bot.get_file(file_id)
            input_path = TEMP_DIR / f"input_{user_id}_{file_id}.mp4"
            
            await bot.download_file(file.file_path, destination=input_path)
            
            await status_msg.edit_text("🎬 <b>Конвертация в Reels формат...</b>\n"
                                      "<i>Это займет 15-30 секунд</i>")
            
            # Конвертация в отдельном потоке
            output_path = await asyncio.get_event_loop().run_in_executor(
                None, create_reels_video_timeweb, input_path, user_id
            )
            
            if output_path and output_path.exists():
                # Чтение и отправка результата
                with open(output_path, 'rb') as f:
                    video_data = f.read()
                
                file_size_mb = output_path.stat().st_size / 1024 / 1024
                
                await message.answer_video(
                    video=BufferedInputFile(video_data, "reels_video.mp4"),
                    caption=f"✅ <b>Готово! Reels видео</b>\n\n"
                           f"📏 Размер: {file_size_mb:.1f} MB\n"
                           f"🎞 Формат: {OUTPUT_WIDTH}×{OUTPUT_HEIGHT}\n"
                           f"⭐ Качество: оптимизировано для соцсетей",
                    supports_streaming=True,
                    width=OUTPUT_WIDTH,
                    height=OUTPUT_HEIGHT
                )
                
                await status_msg.delete()
                
                # Очистка
                input_path.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)
                gc.collect()
                
            else:
                await status_msg.edit_text("❌ <b>Не удалось обработать видео</b>\n\n"
                                          "Возможные причины:\n"
                                          "• Видео слишком длинное (макс. 45 сек)\n"
                                          "• Недостаточно памяти на сервере\n"
                                          "• Попробуйте другое видео")
                
        except Exception as e:
            logger.error(f"Ошибка обработки: {e}")
            await status_msg.edit_text("❌ <b>Ошибка обработки</b>\n"
                                      "Попробуйте еще раз или другое видео")
        finally:
            # Всегда чистим временные файлы
            await cleanup_temp_files()

# --- ОБРАБОТКА ОБЫЧНЫХ ВИДЕО ---

@dp.message(F.video)
async def handle_video(message: Message):
    """Подсказка для обычных видео"""
    await message.answer("📹 <b>Я работаю только с видео-кружками!</b>\n\n"
                        "Чтобы получить Reels видео:\n"
                        "1. Нажмите на <b>скрепку</b> 📎\n"
                        "2. Выберите <b>«Кружочек»</b> 🎬\n"
                        "3. Запишите или выберите видео\n"
                        "4. Отправьте мне!\n\n"
                        "Я преобразую его в вертикальное видео с черным фоном.")

# --- ЗАПУСК БОТА ---

async def on_startup():
    """Действия при запуске"""
    logger.info("=" * 50)
    logger.info("Video Circle Converter Bot запущен!")
    logger.info(f"ID бота: {BOT_TOKEN.split(':')[0]}")
    logger.info(f"Временная директория: {TEMP_DIR}")
    logger.info(f"Лимит памяти: {MEMORY_LIMIT_MB} MB")
    logger.info(f"Потоки FFmpeg: {FFMPEG_THREADS}")
    
    # Проверяем наличие FFmpeg
    try:
        result = subprocess.run(['ffmpeg', '-version'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            logger.info("✅ FFmpeg найден")
        else:
            logger.error("❌ FFmpeg не найден!")
    except:
        logger.error("❌ FFmpeg не найден!")
    
    # Очистка старых файлов
    deleted = await cleanup_temp_files()
    logger.info(f"Очищено старых файлов: {deleted}")
    logger.info("=" * 50)

async def on_shutdown():
    """Действия при остановке"""
    logger.info("Останавливаю бота...")
    deleted = await cleanup_temp_files()
    logger.info(f"Очищено файлов: {deleted}")
    logger.info("Бот остановлен")

async def main():
    """Главная функция"""
    await on_startup()
    
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        await on_shutdown()

if __name__ == "__main__":
    # Оптимизации для Timeweb
    if sys.platform == "linux":
        # Используем uvloop для ускорения asyncio
        try:
            import uvloop
            uvloop.install()
            logger.info("✅ Используется uvloop")
        except:
            pass
    
    # Запуск бота
    asyncio.run(main())
    asyncio.run(main())

