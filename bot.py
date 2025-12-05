#!/usr/bin/env python3
"""
Video Circle to Reels Converter Bot
"""

import os
import sys
import logging
import asyncio
from pathlib import Path
from typing import Optional
import subprocess

# Импорты aiogram 3.7+
from aiogram import Bot, Dispatcher, F, types, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from aiogram.client.default import DefaultBotProperties  # ← ВАЖНО!

import psutil

# Попробуем импортировать moviepy
try:
    import moviepy.editor as mp
    from moviepy.video.VideoClip import ColorClip
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8535285877:AAFkJEwV18KFCnEJPAyTR2AsSsgvQbTA6fg")
TZ = os.getenv("TZ", "Europe/Moscow")

# Лимиты
MAX_VIDEO_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_VIDEO_DURATION = 60

# Настройки видео
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
OUTPUT_FPS = 30

# Пути
TEMP_DIR = Path("/tmp/video_temp")
TEMP_DIR.mkdir(exist_ok=True, parents=True)

# --- ИНИЦИАЛИЗАЦИЯ БОТА (ИСПРАВЛЕНО!) ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# --- ЛОГГИРОВАНИЕ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def cleanup_temp_files():
    """Очистка временных файлов"""
    try:
        deleted = 0
        for file in TEMP_DIR.glob("*"):
            try:
                if file.is_file():
                    file.unlink()
                    deleted += 1
            except:
                continue
        return deleted
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")
        return 0

def check_ffmpeg():
    """Проверка наличия FFmpeg"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            logger.info("✅ FFmpeg найден")
            return True
        else:
            logger.error("❌ FFmpeg не работает")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка проверки FFmpeg: {e}")
        return False

def create_reels_video(input_path: Path, user_id: int) -> Optional[Path]:
    """
    Основная функция конвертации кружка в Reels видео
    """
    if not MOVIEPY_AVAILABLE:
        logger.error("❌ MoviePy не установлен")
        return None
    
    output_path = TEMP_DIR / f"reels_{user_id}_{int(asyncio.get_event_loop().time())}.mp4"
    
    try:
        logger.info(f"Начинаю конвертацию: {input_path.name}")
        
        # Загружаем видео
        video = mp.VideoFileClip(str(input_path), audio=True)
        
        # Проверяем длительность
        if video.duration > MAX_VIDEO_DURATION:
            logger.warning(f"Видео слишком длинное: {video.duration:.1f} сек")
            video.close()
            return None
        
        # Получаем размер оригинального видео
        original_size = video.size
        
        # Определяем размер круга (минимальная сторона)
        circle_size = min(original_size)
        
        # Центрируем обрезку
        x_center = original_size[0] // 2
        y_center = original_size[1] // 2
        half_size = circle_size // 2
        
        # Обрезаем до квадрата
        cropped = video.crop(
            x1=x_center - half_size,
            y1=y_center - half_size,
            x2=x_center + half_size,
            y2=y_center + half_size
        )
        
        # Масштабируем для заполнения высоты
        target_height = OUTPUT_HEIGHT
        scale_factor = target_height / circle_size
        scaled = cropped.resize(scale_factor)
        
        # Создаем черный фон
        background = ColorClip(
            size=(OUTPUT_WIDTH, OUTPUT_HEIGHT),
            color=(0, 0, 0),
            duration=video.duration
        )
        
        # Центрируем видео на фоне
        x_pos = (OUTPUT_WIDTH - scaled.w) // 2
        y_pos = (OUTPUT_HEIGHT - scaled.h) // 2
        
        # Накладываем видео на фон
        final_video = mp.CompositeVideoClip(
            [background, scaled.set_position((x_pos, y_pos))]
        )
        
        # Устанавливаем параметры
        final_video = final_video.set_duration(video.duration)
        final_video = final_video.set_fps(OUTPUT_FPS)
        
        # Добавляем аудио
        if video.audio:
            final_video = final_video.set_audio(video.audio)
        
        # Экспортируем
        logger.info(f"Экспортирую видео: {output_path}")
        
        final_video.write_videofile(
            str(output_path),
            codec='libx264',
            preset='medium',
            bitrate='5M',
            audio_codec='aac',
            audio_bitrate='192k',
            threads=2,
            verbose=False,
            logger=None
        )
        
        # Закрываем клипы
        video.close()
        cropped.close()
        scaled.close()
        background.close()
        final_video.close()
        
        # Проверяем размер
        if output_path.stat().st_size > MAX_VIDEO_SIZE:
            logger.warning(f"Результат слишком большой: {output_path.stat().st_size}")
            output_path.unlink()
            return None
        
        logger.info(f"✅ Конвертация завершена: {output_path.name}")
        return output_path
        
    except Exception as e:
        logger.error(f"❌ Ошибка конвертации: {e}")
        try:
            video.close()
        except:
            pass
        
        if output_path.exists():
            output_path.unlink()
        
        return None

# --- КОМАНДЫ БОТА ---

@dp.message(Command("start", "help"))
async def cmd_start(message: Message):
    """Команда /start"""
    welcome_text = """
🎬 <b>Video Circle to Reels Converter</b>

<u>Я превращаю видео-кружочки Telegram в вертикальные видео формата Reels/TikTok!</u>

<b>📱 Как использовать:</b>
1. Просто отправьте мне <b>видео-кружок</b> (video note)
2. Я автоматически его обработаю
3. Получите готовое видео Reels формата!

<b>🛠 Команды:</b>
/start - это сообщение
/stats - статистика бота
/cleanup - очистка кэша
"""
    
    await message.answer(welcome_text)

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика бота"""
    process = psutil.Process()
    memory = process.memory_info()
    
    stats_text = f"""
📊 <b>Статистика системы:</b>

<b>Память (RAM):</b>
• Используется: {memory.rss / 1024 / 1024:.1f} MB

<b>Процессор (CPU):</b>
• Загрузка: {process.cpu_percent(interval=0.1):.1f}%

<b>Файлы:</b>
• Временных файлов: {len(list(TEMP_DIR.glob('*')))}
"""
    
    await message.answer(stats_text)

@dp.message(Command("cleanup"))
async def cmd_cleanup(message: Message):
    """Очистка временных файлов"""
    deleted = await cleanup_temp_files()
    await message.answer(f"✅ Очищено {deleted} временных файлов")

# --- ОБРАБОТКА ВИДЕО-КРУЖКОВ ---

@dp.message(F.video_note)
async def handle_video_note(message: Message):
    """Обработка видео-кружков"""
    if not MOVIEPY_AVAILABLE:
        await message.answer("❌ <b>MoviePy не установлен!</b>\nБот не может обрабатывать видео.")
        return
    
    user_id = message.from_user.id
    
    status_msg = await message.answer("🔄 <b>Получаю видео-кружок...</b>")
    
    try:
        # Скачиваем видео
        video_note = message.video_note
        file_id = video_note.file_id
        file = await bot.get_file(file_id)
        
        input_path = TEMP_DIR / f"input_{user_id}_{file_id}.mp4"
        await bot.download_file(file.file_path, destination=input_path)
        
        await status_msg.edit_text("🎬 <b>Конвертирую в Reels формат...</b>")
        
        # Конвертируем
        output_path = await asyncio.get_event_loop().run_in_executor(
            None, create_reels_video, input_path, user_id
        )
        
        if output_path and output_path.exists():
            # Читаем результат
            with open(output_path, 'rb') as video_file:
                video_data = video_file.read()
            
            await status_msg.edit_text("📤 <b>Отправляю результат...</b>")
            
            await message.answer_video(
                video=BufferedInputFile(video_data, filename="reels_video.mp4"),
                caption="✅ <b>Готово! Ваше видео в Reels формате</b>",
                supports_streaming=True
            )
            
            # Удаляем временные файлы
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            await status_msg.delete()
            
        else:
            await status_msg.edit_text("❌ <b>Не удалось конвертировать видео</b>")
            
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")
        await status_msg.edit_text("❌ <b>Произошла ошибка</b>")
        await cleanup_temp_files()

@dp.message(F.video)
async def handle_video(message: Message):
    """Информация для обычных видео"""
    await message.answer("📹 <b>Я работаю только с видео-кружками!</b>\n\n"
                        "Чтобы получить Reels видео:\n"
                        "1. Нажмите на скрепку\n"
                        "2. Выберите «Кружочек»\n"
                        "3. Отправьте мне!")

# --- ЗАПУСК БОТА ---

async def main():
    """Главная функция"""
    logger.info("=" * 50)
    logger.info("Video Circle Converter Bot запускается...")
    
    # Проверка FFmpeg
    if not check_ffmpeg():
        logger.error("FFmpeg не найден! Бот не будет работать")
    
    # Проверка MoviePy
    if not MOVIEPY_AVAILABLE:
        logger.error("❌ MoviePy не установлен! Установите: pip install moviepy")
    else:
        logger.info("✅ MoviePy доступен")
    
    # Очищаем временные файлы
    deleted = await cleanup_temp_files()
    logger.info(f"Очищено файлов: {deleted}")
    logger.info("=" * 50)
    
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    # Устанавливаем часовой пояс
    if TZ:
        os.environ['TZ'] = TZ
    
    # Используем uvloop для Linux если доступен
    if sys.platform == 'linux':
        try:
            import uvloop
            uvloop.install()
            logger.info("✅ Используется uvloop")
        except ImportError:
            logger.info("ℹ️ uvloop не установлен")
    
    # Запускаем бота
    asyncio.run(main())
