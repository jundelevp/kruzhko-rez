#!/usr/bin/env python3
"""
Video Circle to Reels Converter Bot
+ HTTP Health Check сервер для Timeweb Cloud
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
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

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
MEMORY_LIMIT_MB = 1800
MAX_VIDEO_SIZE = 40 * 1024 * 1024
MAX_VIDEO_DURATION = 45
MAX_CACHE_SIZE_MB = 100

# 2. CPU (3.3 ГГц, 1-2 ядра на Timeweb)
FFMPEG_THREADS = 2

# 3. КАЧЕСТВО ВЫХОДНОГО ВИДЕО
OUTPUT_WIDTH = 720
OUTPUT_HEIGHT = 1280
OUTPUT_FPS = 30
OUTPUT_PRESET = 'ultrafast'
OUTPUT_CRF = 23
OUTPUT_BITRATE = '2M'

# 4. ПУТИ И ДИРЕКТОРИИ
TEMP_DIR = Path("/tmp/video_bot")
TEMP_DIR.mkdir(exist_ok=True, parents=True)

# 5. HTTP Health Check порт (Timeweb требует)
HEALTH_CHECK_PORT = 8080

# 6. ОПТИМИЗАЦИЯ ПАМЯТИ
gc.enable()
gc.set_threshold(500, 5, 5)

# --- HTTP Health Check сервер ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "ok", "service": "telegram-video-bot"}')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Отключаем логирование HTTP запросов
        pass

def run_health_server():
    """Запускает HTTP сервер для health check"""
    server = HTTPServer(('0.0.0.0', HEALTH_CHECK_PORT), HealthCheckHandler)
    logging.info(f"HTTP Health Check сервер запущен на порту {HEALTH_CHECK_PORT}")
    server.serve_forever()

# --- ЛОГГИРОВАНИЕ ---
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
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# Семафор для ограничения одновременных конвертаций
conversion_semaphore = asyncio.Semaphore(1)

# --- ОПТИМИЗИРОВАННЫЕ ФУНКЦИИ ---
def check_memory_usage() -> bool:
    process = psutil.Process()
    memory = process.memory_info().rss / 1024 / 1024
    if memory > MEMORY_LIMIT_MB * 0.8:
        logger.warning(f"Мало памяти: {memory:.1f} MB из {MEMORY_LIMIT_MB} MB")
        return False
    return True

async def cleanup_temp_files():
    try:
        deleted = 0
        total_size = 0
        files = sorted(TEMP_DIR.glob("*"), key=lambda x: x.stat().st_mtime)
        cache_size_mb = sum(f.stat().st_size for f in files) / 1024 / 1024
        
        if cache_size_mb > MAX_CACHE_SIZE_MB:
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
    output_path = TEMP_DIR / f"reels_{user_id}.mp4"
    
    try:
        if not check_memory_usage():
            logger.error("Недостаточно памяти для обработки")
            return None
        
        logger.info(f"Конвертация: {input_path.name}")
        
        video = mp.VideoFileClip(str(input_path), audio=True, target_resolution=(480, 480))
        
        if video.duration > MAX_VIDEO_DURATION:
            logger.warning(f"Слишком длинное видео: {video.duration:.1f}с")
            video.close()
            return None
        
        target_height = OUTPUT_HEIGHT
        original_size = video.size
        circle_size = min(original_size)
        
        scale_factor = target_height / circle_size
        if scale_factor > 3:
            scale_factor = 2
        
        background = ColorClip(
            size=(OUTPUT_WIDTH, OUTPUT_HEIGHT),
            color=(0, 0, 0),
            duration=video.duration
        ).set_fps(OUTPUT_FPS)
        
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
        x_pos = (OUTPUT_WIDTH - scaled.w) // 2
        y_pos = (OUTPUT_HEIGHT - scaled.h) // 2
        
        final_video = mp.CompositeVideoClip(
            [background, scaled.set_position((x_pos, y_pos))],
            size=(OUTPUT_WIDTH, OUTPUT_HEIGHT),
            use_bgclip=True
        ).set_duration(video.duration)
        
        if video.audio:
            final_video = final_video.set_audio(video.audio)
        
        logger.info("Экспорт видео...")
        
        ffmpeg_params = [
            '-threads', str(FFMPEG_THREADS),
            '-preset', OUTPUT_PRESET,
            '-crf', str(OUTPUT_CRF),
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            '-max_muxing_queue_size', '9999',
            '-bufsize', '2000k'
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
            logger=None
        )
        
        video.close()
        cropped.close()
        scaled.close()
        background.close()
        final_video.close()
        gc.collect()
        
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
    process = psutil.Process()
    memory = process.memory_info()
    memory_used = memory.rss / 1024 / 1024
    memory_percent = (memory_used / MEMORY_LIMIT_MB) * 100
    cpu_percent = process.cpu_percent(interval=0.5)
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
    deleted = await cleanup_temp_files()
    await message.answer(f"✅ Очищено {deleted} временных файлов")

# --- ОБРАБОТКА ВИДЕО-КРУЖКОВ ---
@dp.message(F.video_note)
async def handle_video_note(message: Message):
    async with conversion_semaphore:
        user_id = message.from_user.id
        
        if not check_memory_usage():
            await message.answer("⚠️ <b>Сервер перегружен</b>\nПопробуйте через несколько минут")
            return
        
        status_msg = await message.answer("🔄 <b>Начинаю обработку...</b>")
        
        try:
            file_id = message.video_note.file_id
            file = await bot.get_file(file_id)
            input_path = TEMP_DIR / f"input_{user_id}_{file_id}.mp4"
            
            await bot.download_file(file.file_path, destination=input_path)
            
            await status_msg.edit_text("🎬 <b>Конвертация в Reels формат...</b>\n<i>Это займет 15-30 секунд</i>")
            
            output_path = await asyncio.get_event_loop().run_in_executor(
                None, create_reels_video_timeweb, input_path, user_id
            )
            
            if output_path and output_path.exists():
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
            await cleanup_temp_files()

@dp.message(F.video)
async def handle_video(message: Message):
    await message.answer("📹 <b>Я работаю только с видео-кружками!</b>\n\n"
                        "Чтобы получить Reels видео:\n"
                        "1. Нажмите на <b>скрепку</b> 📎\n"
                        "2. Выберите <b>«Кружочек»</b> 🎬\n"
                        "3. Запишите или выберите видео\n"
                        "4. Отправьте мне!\n\n"
                        "Я преобразую его в вертикальное видео с черным фоном.")

# --- ЗАПУСК ---
async def main():
    """Главная функция с запуском HTTP сервера"""
    logger.info("=" * 50)
    logger.info("Video Circle Converter Bot запущен!")
    logger.info(f"ID бота: {BOT_TOKEN.split(':')[0]}")
    logger.info(f"Временная директория: {TEMP_DIR}")
    logger.info(f"Лимит памяти: {MEMORY_LIMIT_MB} MB")
    logger.info(f"Потоки FFmpeg: {FFMPEG_THREADS}")
    logger.info(f"Health Check порт: {HEALTH_CHECK_PORT}")
    
    # Проверка FFmpeg
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        logger.info("✅ FFmpeg установлен")
    except:
        logger.error("❌ FFmpeg не найден!")
        sys.exit(1)
    
    # Очистка при старте
    await cleanup_temp_files()
    
    # Запуск HTTP сервера в отдельном потоке
    http_thread = threading.Thread(target=run_health_server, daemon=True)
    http_thread.start()
    logger.info("✅ HTTP Health Check сервер запущен")
    
    # Запуск бота
    logger.info("✅ Запускаю Telegram бота...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Оптимизации для Timeweb
    if sys.platform == "linux":
        try:
            import uvloop
            uvloop.install()
            logger.info("✅ Используется uvloop")
        except:
            pass
    
    # Запуск
    asyncio.run(main())
