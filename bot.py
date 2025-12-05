#!/usr/bin/env python3
"""
Video Circle to Reels Converter Bot
Преобразует видео-кружочки Telegram в вертикальные видео Reels/TikTok формата
с черным фоном и высоким качеством.
"""

import os
import sys
import logging
import gc
import asyncio
from pathlib import Path
from typing import Optional
import subprocess

from aiogram import Bot, Dispatcher, F, types, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
import moviepy.editor as mp
from moviepy.video.fx.all import resize
import psutil

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = "8535285877:AAFkJEwV18KFCnEJPAyTR2AsSsgvQbTA6fg"

# Лимиты для Timeweb (2 ГБ ОЗУ)
MAX_VIDEO_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_VIDEO_DURATION = 60  # Макс. длительность кружка
OUTPUT_DURATION_LIMIT = 90  # Макс. длительность результата

# Настройки выходного видео (Reels/TikTok формат)
OUTPUT_WIDTH = 1080  # Full HD вертикальное
OUTPUT_HEIGHT = 1920
OUTPUT_FPS = 30
OUTPUT_CODEC = 'libx264'
OUTPUT_PRESET = 'medium'
OUTPUT_BITRATE = '5M'  # Высокое качество

# Пути
TEMP_DIR = Path("video_temp")
TEMP_DIR.mkdir(exist_ok=True)

# Оптимизация памяти
gc.enable()
gc.set_threshold(700, 10, 10)

# --- ЛОГГИРОВАНИЕ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
router = Router()
dp.include_router(router)

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

def create_reels_video(input_path: Path, user_id: int) -> Optional[Path]:
    """
    Основная функция конвертации кружка в Reels видео
    с черным фоном и высоким качеством
    """
    output_path = TEMP_DIR / f"reels_{user_id}_{int(asyncio.get_event_loop().time())}.mp4"
    
    try:
        logger.info(f"Начинаю конвертацию: {input_path.name}")
        
        # Загружаем видео с оптимизацией памяти
        video = mp.VideoFileClip(str(input_path), audio=True)
        
        # Проверяем длительность
        if video.duration > MAX_VIDEO_DURATION:
            logger.warning(f"Видео слишком длинное: {video.duration:.1f} сек")
            video.close()
            return None
        
        # Получаем размер оригинального видео (круг)
        original_size = video.size
        logger.info(f"Оригинальный размер: {original_size}")
        
        # Вычисляем параметры для вертикального видео
        # Определяем размер круга (квадрат)
        circle_size = min(original_size)
        
        # Обрезаем до квадрата (центрируем круг)
        x_center = original_size[0] // 2
        y_center = original_size[1] // 2
        half_size = circle_size // 2
        
        # Обрезаем видео до квадрата
        cropped = video.crop(
            x1=x_center - half_size,
            y1=y_center - half_size,
            x2=x_center + half_size,
            y2=y_center + half_size
        )
        
        # Создаем черный фон (вертикальное видео)
        # Вычисляем масштаб для заполнения высоты
        target_height = OUTPUT_HEIGHT
        scale_factor = target_height / circle_size
        
        # Увеличиваем круг для заполнения высоты
        scaled = cropped.resize(scale_factor)
        
        # Создаем черный фон нужного размера
        from moviepy.video.VideoClip import ColorClip
        background = ColorClip(
            size=(OUTPUT_WIDTH, OUTPUT_HEIGHT),
            color=(0, 0, 0),  # Черный цвет
            duration=video.duration
        )
        
        # Центрируем увеличенный круг на черном фоне
        x_pos = (OUTPUT_WIDTH - scaled.w) // 2
        y_pos = (OUTPUT_HEIGHT - scaled.h) // 2
        
        # Накладываем видео на фон
        final_video = mp.CompositeVideoClip(
            [background, scaled.set_position((x_pos, y_pos))]
        )
        
        # Устанавливаем длительность и FPS
        final_video = final_video.set_duration(video.duration)
        final_video = final_video.set_fps(OUTPUT_FPS)
        
        # Добавляем аудио
        if video.audio:
            final_video = final_video.set_audio(video.audio)
        
        # Экспортируем с высоким качеством
        logger.info(f"Экспортирую видео: {output_path}")
        
        # Используем FFmpeg для лучшего качества
        final_video.write_videofile(
            str(output_path),
            codec=OUTPUT_CODEC,
            preset=OUTPUT_PRESET,
            bitrate=OUTPUT_BITRATE,
            audio_codec='aac',
            audio_bitrate='192k',
            temp_audiofile=str(TEMP_DIR / "temp_audio.m4a"),
            remove_temp=True,
            threads=2,  # Ограничиваем потоки для 2 ГБ ОЗУ
            ffmpeg_params=[
                '-pix_fmt', 'yuv420p',  # Совместимость с соцсетями
                '-movflags', '+faststart'  # Быстрый старт для онлайн
            ]
        )
        
        # Закрываем клипы для освобождения памяти
        video.close()
        cropped.close()
        scaled.close()
        background.close()
        final_video.close()
        
        # Проверяем размер результата
        if output_path.stat().st_size > MAX_VIDEO_SIZE:
            logger.warning(f"Результат слишком большой: {output_path.stat().st_size}")
            output_path.unlink()
            return None
        
        logger.info(f"✅ Конвертация завершена: {output_path.name}")
        return output_path
        
    except Exception as e:
        logger.error(f"❌ Ошибка конвертации: {e}")
        # Закрываем видео если открыто
        try:
            video.close()
        except:
            pass
        
        # Удаляем временные файлы при ошибке
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

<b>✨ Что я делаю:</b>
• Беру ваш видео-кружок из Telegram
• Увеличиваю его с сохранением качества
• Добавляю черный фон
• Создаю вертикальное видео 1080×1920
• Сохраняю высокое качество и звук

<b>📱 Как использовать:</b>
1. Просто отправьте мне <b>видео-кружок</b> (video note)
2. Я автоматически его обработаю
3. Получите готовое видео Reels формата!

<b>⚡ Особенности:</b>
• Черный фон (без белого!)
• Вертикальный формат 9:16
• Высокое качество видео
• Сохранение оригинального звука
• Быстрая обработка

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
• Всего виртуальной: {memory.vms / 1024 / 1024:.1f} MB

<b>Процессор (CPU):</b>
• Загрузка: {process.cpu_percent(interval=0.1):.1f}%

<b>Файлы:</b>
• Временных файлов: {len(list(TEMP_DIR.glob('*')))}
• Свободно места: {psutil.disk_usage('/').free / 1024 / 1024 / 1024:.1f} GB
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
    user_id = message.from_user.id
    
    # Отправляем статус
    status_msg = await message.answer("🔄 <b>Получаю видео-кружок...</b>")
    
    try:
        # Скачиваем видео-кружок
        video_note = message.video_note
        file_id = video_note.file_id
        
        # Получаем информацию о файле
        file = await bot.get_file(file_id)
        file_path = file.file_path
        
        # Создаем имя для временного файла
        input_path = TEMP_DIR / f"input_{user_id}_{file_id}.mp4"
        
        # Скачиваем файл
        await bot.download_file(file_path, destination=input_path)
        
        await status_msg.edit_text("🎬 <b>Конвертирую в Reels формат...</b>\n<i>Это займет несколько секунд</i>")
        
        # Конвертируем
        output_path = await asyncio.get_event_loop().run_in_executor(
            None, create_reels_video, input_path, user_id
        )
        
        if output_path and output_path.exists():
            # Читаем результат
            with open(output_path, 'rb') as video_file:
                video_data = video_file.read()
            
            # Отправляем результат
            await status_msg.edit_text("📤 <b>Отправляю результат...</b>")
            
            await message.answer_video(
                video=BufferedInputFile(video_data, filename="reels_video.mp4"),
                caption="✅ <b>Готово! Ваше видео в Reels формате</b>\n\n"
                       f"Размер: {output_path.stat().st_size / 1024 / 1024:.1f} MB\n"
                       f"Формат: 1080×1920 (вертикальный)\n"
                       f"Качество: высокое",
                supports_streaming=True
            )
            
            # Удаляем временные файлы
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            await status_msg.delete()
            
        else:
            await status_msg.edit_text("❌ <b>Не удалось конвертировать видео</b>\n\n"
                                      "Возможные причины:\n"
                                      "• Видео слишком длинное\n"
                                      "• Ошибка обработки\n"
                                      "• Попробуйте другое видео")
            
    except Exception as e:
        logger.error(f"Ошибка обработки видео-кружка: {e}")
        await status_msg.edit_text("❌ <b>Произошла ошибка при обработке</b>\n\n"
                                  "Попробуйте еще раз или отправьте другое видео")
        # Очищаем временные файлы при ошибке
        await cleanup_temp_files()

# --- ОБРАБОТКА ОБЫЧНЫХ ВИДЕО ---

@dp.message(F.video)
async def handle_video(message: Message):
    """Информация для обычных видео"""
    await message.answer("📹 <b>Я работаю только с видео-кружками!</b>\n\n"
                        "Чтобы получить Reels видео:\n"
                        "1. Нажмите на <b>скрепку</b> в поле ввода\n"
                        "2. Выберите <b>«Кружочек»</b> (видео заметка)\n"
                        "3. Запишите или выберите видео\n"
                        "4. Отправьте мне!\n\n"
                        "Я преобразую его в вертикальное видео с черным фоном.")

# --- ЗАПУСК БОТА ---

async def on_startup():
    """Действия при запуске"""
    logger.info("=" * 50)
    logger.info("Video Circle Converter Bot запущен!")
    logger.info(f"ID бота: {BOT_TOKEN.split(':')[0]}")
    logger.info(f"Временная директория: {TEMP_DIR.absolute()}")
    logger.info("Очищаю старые временные файлы...")
    
    deleted = await cleanup_temp_files()
    logger.info(f"Очищено файлов: {deleted}")
    
    # Проверяем наличие FFmpeg
    try:
        result = subprocess.run(['ffmpeg', '-version'], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("✅ FFmpeg найден")
        else:
            logger.error("❌ FFmpeg не найден! Установите: sudo apt install ffmpeg")
    except:
        logger.error("❌ FFmpeg не найден! Установите: sudo apt install ffmpeg")
    
    logger.info("=" * 50)

async def on_shutdown():
    """Действия при остановке"""
    logger.info("Останавливаю бота...")
    deleted = await cleanup_temp_files()
    logger.info(f"Очищено файлов при остановке: {deleted}")
    logger.info("Бот остановлен")

async def main():
    """Главная функция"""
    await on_startup()
    
    try:
        await dp.start_polling(bot, on_startup=on_startup, on_shutdown=on_shutdown)
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        await on_shutdown()

if __name__ == "__main__":
    # Устанавливаем оптимизации для asyncio
    if sys.platform == 'linux':
        import uvloop
        uvloop.install()
    
    # Запускаем бота
    asyncio.run(main())