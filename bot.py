import os
import json
import tempfile
import logging
import asyncio
import subprocess
from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, VideoNote, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    BufferedInputFile
)
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from aiogram.client.session.aiohttp import AiohttpSession
import time
import psutil
from datetime import datetime

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "8535285877:AAFkJEwV18KFCnEJPAyTR2AsSsgvQbTA6fg")
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "default_secret_token_123")

if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не установлен!")
    exit(1)

print(f"✨ Бот запускается... Токен: {BOT_TOKEN[:10]}...")

MAX_VIDEO_DURATION = 60
FREE_LIMIT = 1
SUPPORT_USERNAME = "Oblastyle"
MAX_FILE_SIZE_MB = 50  # Максимальный размер файла в MB
MAX_CPU_PERCENT = 80   # Максимальная загрузка CPU
MAX_MEMORY_PERCENT = 85 # Максимальная загрузка памяти

# === ЛОГИРОВАНИЕ ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# === ПУТИ ===
BASE_DIR = Path(__file__).parent
USERS_FILE = BASE_DIR / "data" / "users.json"
TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)
USERS_FILE.parent.mkdir(exist_ok=True)

user_locks = {}
# Ограничиваем воркеры для слабого хостинга
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video_processor")

# === ИНИЦИАЛИЗАЦИЯ БОТА ===
session = AiohttpSession(timeout=60)  # Увеличиваем таймаут
bot = Bot(
    token=BOT_TOKEN,
    session=session,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()
router = Router()

# === МОНИТОРИНГ РЕСУРСОВ ===
def check_system_resources():
    """Проверка загрузки системы"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory_percent = psutil.virtual_memory().percent
        
        logger.info(f"📊 Мониторинг: CPU={cpu_percent}%, RAM={memory_percent}%")
        
        if cpu_percent > MAX_CPU_PERCENT:
            logger.warning(f"⚠️ Высокая загрузка CPU: {cpu_percent}%")
            return False
            
        if memory_percent > MAX_MEMORY_PERCENT:
            logger.warning(f"⚠️ Высокая загрузка памяти: {memory_percent}%")
            return False
            
        return True
    except Exception as e:
        logger.error(f"Ошибка мониторинга: {e}")
        return True

# === ПРОВЕРКА FFMPEG ===
def check_ffmpeg():
    """Проверка наличия FFmpeg с версией"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            version_line = result.stdout.split('\n')[0]
            logger.info(f"✅ FFmpeg найден: {version_line[:50]}")
            
            # Проверяем наличие кодеков
            codec_check = subprocess.run(
                ['ffmpeg', '-codecs'], capture_output=True, text=True, timeout=5
            )
            if 'libx264' in codec_check.stdout:
                logger.info("✅ Кодек libx264 доступен")
            else:
                logger.warning("⚠️ Кодек libx264 недоступен")
                
            return True
        else:
            logger.warning("❌ FFmpeg не найден!")
            return False
    except subprocess.TimeoutExpired:
        logger.warning("⏱️ Таймаут проверки FFmpeg")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка проверки FFmpeg: {e}")
        return False

ffmpeg_available = check_ffmpeg()

# === ОПТИМИЗИРОВАННАЯ ОБРАБОТКА ВИДЕО (Reels стиль) ===
async def async_process_video_reels(input_path: str, output_path: str, duration: float):
    """Оптимизированная обработка видео в Reels стиле с черными полосами"""
    
    def _process():
        try:
            if not os.path.exists(input_path):
                raise FileNotFoundError(f"Входной файл не найден: {input_path}")
            
            file_size = os.path.getsize(input_path)
            logger.info(f"🎞️ Начало обработки Reels. Размер: {file_size/1024/1024:.2f} MB")
            
            if not check_system_resources():
                logger.warning("⚠️ Высокая нагрузка на систему, снижаем качество обработки")
                quality_preset = 'ultrafast'  # Самая быстрая обработка
                crf_value = '28'  # Немного хуже качество, но быстрее
            else:
                quality_preset = 'fast'  # Баланс скорости и качества
                crf_value = '26'  # Хорошее качество
            
            # ОПТИМИЗИРОВАННАЯ КОМАНДА ДЛЯ REELS:
            # 1. Масштабируем с сохранением пропорций
            # 2. Добавляем черные полосы для вертикального формата
            # 3. Оптимизированные настройки для слабого хостинга
            
            cmd = [
                'ffmpeg',
                '-i', input_path,
                '-hide_banner',  # Скрываем лишнюю информацию
                '-loglevel', 'error',  # Только ошибки
                # Вертикальное видео 1080x1920 с черными полосами
                '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,'
                       'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,'
                       'setsar=1',  # Устанавливаем SAR=1 для правильного отображения
                # Оптимизированные настройки видео
                '-c:v', 'libx264',
                '-preset', quality_preset,  # Используем быстрый пресет
                '-crf', crf_value,          # Качество (меньше = лучше)
                '-tune', 'fastdecode',      # Оптимизация для декодирования
                '-profile:v', 'baseline',   # Совместимость со всеми устройствами
                '-pix_fmt', 'yuv420p',      # Самый совместимый формат пикселей
                '-movflags', '+faststart',  # Быстрый старт для веба
                '-g', '30',                 # Частота ключевых кадров
                # Аудио настройки
                '-c:a', 'aac',
                '-b:a', '96k',              # Низкий битрейт аудио
                '-ac', '2',                 # Стерео звук
                '-ar', '44100',             # Частота дискретизации
                # Ограничение битрейта для экономии места
                '-maxrate', '1500k',
                '-bufsize', '3000k',
                '-y',  # Перезапись без подтверждения
                output_path
            ]
            
            logger.info(f"🔄 Запуск обработки Reels (preset: {quality_preset})...")
            
            start_time = time.time()
            
            # Запускаем с ограничением ресурсов
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # Ограничиваем приоритет процесса для Linux
                preexec_fn=lambda: os.nice(10) if hasattr(os, 'nice') else None
            )
            
            try:
                stdout, stderr = process.communicate(timeout=120)  # 2 минуты максимум
                
                if process.returncode == 0:
                    processing_time = time.time() - start_time
                    output_size = os.path.getsize(output_path)
                    
                    logger.info(f"✅ Reels видео обработано за {processing_time:.1f} сек!")
                    logger.info(f"📦 Размер: {output_size/1024/1024:.2f} MB")
                    
                    # Быстрая проверка результата
                    if os.path.exists(output_path) and output_size > 0:
                        # Проверяем основные параметры
                        check_cmd = [
                            'ffprobe',
                            '-v', 'quiet',
                            '-select_streams', 'v:0',
                            '-show_entries', 'stream=width,height,duration,codec_name,bit_rate',
                            '-of', 'json',
                            output_path
                        ]
                        
                        try:
                            check_result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
                            if check_result.returncode == 0:
                                info = json.loads(check_result.stdout)
                                streams = info.get('streams', [])
                                if streams:
                                    stream = streams[0]
                                    logger.info(f"📐 Результат: {stream.get('width')}x{stream.get('height')}, "
                                               f"кодек: {stream.get('codec_name')}")
                        except:
                            pass  # Не критично
                        
                        return True
                    else:
                        logger.error("❌ Выходной файл не создан или пустой")
                        return False
                else:
                    logger.error(f"❌ FFmpeg ошибка (код: {process.returncode}): {stderr[:200]}")
                    
                    # 🔄 Резервный вариант - простое копирование с черными полосами
                    logger.info("🔄 Пробую резервный вариант...")
                    
                    backup_cmd = [
                        'ffmpeg',
                        '-i', input_path,
                        '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,'
                               'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black',
                        '-c:v', 'libx264',
                        '-preset', 'ultrafast',
                        '-c:a', 'copy',  # Копируем аудио без изменений
                        '-y',
                        output_path
                    ]
                    
                    backup_result = subprocess.run(backup_cmd, capture_output=True, text=True, timeout=60)
                    if backup_result.returncode == 0 and os.path.exists(output_path):
                        logger.info("✅ Резервная обработка успешна")
                        return True
                    else:
                        return False
                        
            except subprocess.TimeoutExpired:
                logger.error("⏱️ Таймаут обработки видео")
                process.kill()
                return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки Reels: {e}")
            return False

    loop = asyncio.get_event_loop()
    try:
        # Ограничиваем общее время выполнения
        return await asyncio.wait_for(
            loop.run_in_executor(executor, _process),
            timeout=180.0  # 3 минуты максимум
        )
    except asyncio.TimeoutError:
        logger.error("⏱️ Общий таймаут обработки Reels")
        return False

# === БЕЗОПАСНАЯ РАБОТА С JSON ===
@contextmanager
def safe_json_write(filepath):
    temp_path = str(filepath) + ".tmp"
    try:
        yield temp_path
        os.replace(temp_path, filepath)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        logger.error(f"Ошибка записи JSON: {e}")
        raise e

def load_users():
    if not os.path.exists(USERS_FILE):
        logger.info(f"Файл {USERS_FILE} не найден, создаю новый")
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            logger.info(f"Загружено {len(data)} пользователей")
            return data
    except json.JSONDecodeError:
        logger.warning(f"Ошибка чтения {USERS_FILE}, создаю новый")
        return {}
    except Exception as e:
        logger.error(f"Ошибка загрузки {USERS_FILE}: {e}")
        return {}

def save_users(users):
    try:
        with safe_json_write(USERS_FILE) as temp_path:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(users, f, ensure_ascii=False, indent=2)
        logger.info(f"Сохранено {len(users)} пользователей")
    except Exception as e:
        logger.error(f"Ошибка сохранения {USERS_FILE}: {e}")

# === КЛАВИАТУРЫ ===
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Создать Reels", callback_data="make_reels")],
        [InlineKeyboardButton(text="📱 Инструкция", callback_data="howto")],
        [InlineKeyboardButton(text="⭐ Премиум", callback_data="premium")],
        [InlineKeyboardButton(text="🛠 Поддержка", callback_data="support")]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_main")]
    ])

def get_format_keyboard():
    """Клавиатура выбора формата"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Reels (вертикальный)", callback_data="format_reels")],
        [InlineKeyboardButton(text="⬜ Квадрат", callback_data="format_square")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])

# === ОБРАБОТКА СООБЩЕНИЙ ===
@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = str(message.from_user.id)
    logger.info(f"🚀 Команда /start от {user_id}")
    
    users = load_users()
    user_data = users.get(user_id, {"free_used": False, "used": 0, "formats": {}})
    remaining_free = 0 if user_data.get("free_used") else 1
    
    welcome_text = (
        "✨ **Добро пожаловать в ReelsMaker!** ✨\n\n"
        "🎬 **Я превращаю кружки Telegram в модные Reels!**\n\n"
        "✅ **Что я делаю:**\n"
        "• Создаю вертикальные видео 9:16\n"
        "• Добавляю черные полосы\n"
        "• Оптимизирую для Instagram/TikTok\n"
        "• Сохраняю качество\n"
        "• Быстрая обработка!\n\n"
        f"🎁 **Бесплатных попыток: {remaining_free}**\n\n"
        "_Выберите действие ниже:_ 👇"
    )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@router.callback_query(F.data == "make_reels")
async def btn_make_reels(callback: CallbackQuery):
    instruction = (
        "🎬 **Создание Reels видео:**\n\n"
        "1. 📱 **Запишите кружок** в Telegram\n"
        "   _Зажмите микрофон → проведите вверх → снимите видео_\n\n"
        "2. 📤 **Перешлите его мне**\n"
        "   _Просто перешлите как обычное сообщение_\n\n"
        "3. ⚡ **Получите готовый Reels**\n"
        "   _Вертикальное видео 1080x1920 с черными полосами!_\n\n"
        "📏 **Формат:** 1080x1920 (9:16)\n"
        "🎨 **Стиль:** Черные полосы\n"
        "⏱️ **Время:** 30-90 секунд\n\n"
        "⬇️ **Перешлите кружок прямо сейчас!**"
    )
    
    await callback.message.edit_text(instruction, reply_markup=get_back_keyboard())
    await callback.answer()

@router.callback_query(F.data == "format_reels")
async def btn_format_reels(callback: CallbackQuery):
    instruction = (
        "🎬 **Формат Reels:**\n\n"
        "📱 **Идеально для:**\n"
        "• Instagram Reels\n"
        "• TikTok\n"
        "• YouTube Shorts\n"
        "• Все вертикальные платформы\n\n"
        "📏 **Размер:** 1080x1920 пикселей\n"
        "🎨 **Особенность:** Черные полосы по бокам\n"
        "⚡ **Преимущество:** Всегда в кадре!\n\n"
        "⬇️ **Перешлите кружок для обработки!**"
    )
    
    await callback.message.edit_text(instruction, reply_markup=get_back_keyboard())
    await callback.answer()

# === ОСНОВНАЯ ОБРАБОТКА КРУЖКА (Reels версия) ===
@router.message(F.video_note)
async def handle_video_note(message: Message):
    user_id = str(message.from_user.id)
    logger.info(f"🎬 ПОЛУЧЕН КРУЖОК от {user_id}")
    
    if user_id in user_locks:
        await message.answer("⏳ Уже обрабатываю ваш предыдущий кружок...")
        return

    lock = asyncio.Future()
    user_locks[user_id] = lock

    try:
        users = load_users()
        user_data = users.get(user_id, {
            "free_used": False, 
            "used": 0,
            "username": message.from_user.username,
            "last_activity": datetime.now().isoformat()
        })

        if not user_data["free_used"]:
            user_data["free_used"] = True
            is_free = True
            logger.info(f"🎁 Пользователь {user_id} использует бесплатный кружок")
        else:
            await message.answer(
                "⚠️ **Бесплатные попытки закончились**\n\n"
                "Напишите @Oblastyle для получения дополнительных обработок!",
                reply_markup=get_main_keyboard()
            )
            return

        video_note: VideoNote = message.video_note
        
        # Проверка размера файла
        if video_note.file_size and video_note.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
            await message.answer(
                f"❌ **Слишком большой файл**\n\n"
                f"Максимум: {MAX_FILE_SIZE_MB} MB\n"
                f"Запишите более короткий кружок! 🎬"
            )
            return
            
        if video_note.duration > MAX_VIDEO_DURATION:
            await message.answer(
                f"❌ **Слишком длинный кружок**\n\n"
                f"Максимум: {MAX_VIDEO_DURATION} секунд\n"
                f"Ваш: {video_note.duration} секунд\n\n"
                "Запишите более короткий кружок! 🎬"
            )
            return

        # Отправляем сообщение о начале обработки
        processing_msg = await message.answer(
            "🔄 **Начинаю обработку Reels...**\n\n"
            "✨ **Что делаю:**\n"
            "• Конвертирую в вертикальный формат\n"
            "• Добавляю черные полосы\n"
            "• Оптимизирую для соцсетей\n"
            "• Сжимаю без потери качества\n\n"
            "⏱️ **Примерное время:** 30-90 секунд\n"
            "_Не закрывайте Telegram..._"
        )

        # Используем временную папку для обработки
        user_temp_dir = TEMP_DIR / user_id
        user_temp_dir.mkdir(exist_ok=True, parents=True)
        
        timestamp = int(time.time())
        input_path = user_temp_dir / f"input_{timestamp}.mp4"
        output_path = user_temp_dir / f"reels_{timestamp}.mp4"
        
        logger.info(f"📥 Скачиваю файл в {input_path}...")
        
        try:
            await bot.download(video_note, destination=input_path)
            
            if not os.path.exists(input_path):
                raise FileNotFoundError("Файл не скачан")
            
            file_size = os.path.getsize(input_path)
            logger.info(f"✅ Файл скачан: {file_size/1024/1024:.2f} MB")
            
        except Exception as e:
            logger.error(f"❌ Ошибка скачивания: {e}")
            await message.answer(
                "❌ **Не удалось скачать кружок**\n\n"
                "Попробуйте отправить его еще раз! 🔄"
            )
            return

        try:
            logger.info("⚡ Начинаю обработку Reels видео...")
            success = await async_process_video_reels(str(input_path), str(output_path), video_note.duration)
            
            if success and os.path.exists(output_path):
                output_size = os.path.getsize(output_path)
                logger.info(f"✅ Reels обработан! Размер: {output_size/1024/1024:.2f} MB")
                
                try:
                    await processing_msg.delete()
                except:
                    pass
                
                # Отправляем результат
                with open(output_path, 'rb') as f:
                    video_bytes = f.read()
                
                await message.answer_video(
                    video=BufferedInputFile(video_bytes, filename="reels_ready.mp4"),
                    caption=(
                        "🎉 **REELS ГОТОВ!** 🎉\n\n"
                        "✅ **Кружок успешно преобразован в Reels!**\n\n"
                        "📱 **Идеально для:**\n"
                        "• Instagram Reels\n"
                        "• TikTok\n"
                        "• YouTube Shorts\n"
                        "• Всех вертикальных платформ\n\n"
                        "📏 **Формат:** 1080x1920 (9:16)\n"
                        "🎨 **Стиль:** Черные полосы\n"
                        "⚡ **Качество:** Оптимизировано\n\n"
                        "_Сохраняйте и делитесь в соцсетях!_ 📲✨"
                    ),
                    supports_streaming=True
                )
                logger.info(f"✅ Reels отправлен пользователю {user_id}")
                
                # Очищаем временные файлы
                try:
                    os.remove(input_path)
                    os.remove(output_path)
                    if os.path.exists(user_temp_dir) and not os.listdir(user_temp_dir):
                        os.rmdir(user_temp_dir)
                except:
                    pass
                
                if is_free:
                    await message.answer(
                        "🎁 **Это была ваша бесплатная обработка!**\n\n"
                        "Хотите больше? Пишите @Oblastyle для премиум доступа! ⭐",
                        reply_markup=get_main_keyboard()
                    )
            else:
                raise RuntimeError("Ошибка обработки Reels")
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки Reels: {e}")
            await message.answer(
                "❌ **Не удалось обработать кружок в Reels**\n\n"
                "Попробуйте еще раз или напишите @Oblastyle 📞",
                reply_markup=get_main_keyboard()
            )
            
            # Очищаем временные файлы при ошибке
            try:
                if os.path.exists(input_path):
                    os.remove(input_path)
                if os.path.exists(output_path):
                    os.remove(output_path)
            except:
                pass

        # Сохраняем данные пользователя
        user_data["last_processed"] = datetime.now().isoformat()
        users[user_id] = user_data
        save_users(users)

    except Exception as e:
        logger.error(f"🚨 Критическая ошибка: {e}")
        await message.answer(
            "⚠️ **Произошла ошибка**\n\n"
            "Пожалуйста, попробуйте позже или напишите @Oblastyle",
            reply_markup=get_main_keyboard()
        )
    finally:
        lock.set_result(True)
        user_locks.pop(user_id, None)
        logger.info(f"🏁 Обработка завершена для {user_id}")

# === ДОПОЛНИТЕЛЬНЫЕ КОМАНДЫ ===
@router.message(Command("status"))
async def cmd_status(message: Message):
    users = load_users()
    total_users = len(users)
    
    # Проверяем системные ресурсы
    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    
    status_text = (
        "📊 **СТАТУС СИСТЕМЫ**\n\n"
        "✅ **Бот работает**\n"
        "👥 **Пользователей:** {}\n"
        "⚡ **Активных обработок:** {}\n"
        "💾 **Память:** {}% ({} MB свободно)\n"
        "🔥 **CPU:** {}%\n"
        "🔧 **FFmpeg:** {}\n"
        "📁 **Временные файлы:** {}\n"
        "🌐 **Режим:** {}\n\n"
        "_Обновлено: {}_"
    ).format(
        total_users,
        len(user_locks),
        memory.percent,
        memory.available // 1024 // 1024,
        cpu_percent,
        "✅ Доступен" if ffmpeg_available else "❌ Недоступен",
        "Очищены" if not os.listdir(TEMP_DIR) else "Есть",
        "вебхук" if os.getenv('RENDER_EXTERNAL_URL') else "polling",
        datetime.now().strftime("%H:%M:%S")
    )
    
    await message.answer(status_text)

@router.message(Command("cleanup"))
async def cmd_cleanup(message: Message):
    """Очистка временных файлов"""
    try:
        # Удаляем только старые файлы (старше 1 часа)
        deleted_count = 0
        current_time = time.time()
        
        for item in TEMP_DIR.rglob("*"):
            if item.is_file():
                try:
                    # Проверяем время создания
                    file_age = current_time - item.stat().st_mtime
                    if file_age > 3600:  # Старше 1 часа
                        item.unlink()
                        deleted_count += 1
                except:
                    continue
        
        await message.answer(f"🧹 Очистка завершена! Удалено файлов: {deleted_count}")
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")
        await message.answer("❌ Ошибка при очистке")

# === ОБРАБОТКА ОСТАЛЬНЫХ СООБЩЕНИЙ ===
@router.message()
async def handle_other(message: Message):
    if message.text and "@Oblastyle" in message.text:
        await message.answer(
            "✅ **Связь с поддержкой установлена!**\n\n"
            "Скоро с вами свяжутся! 📞\n\n"
            "А пока можете попробовать создать Reels! 🎬",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            "🎬 **ReelsMaker** 🎬\n\n"
            "Я превращаю кружки Telegram в стильные Reels видео!\n\n"
            "✨ **Просто перешлите мне кружок**\n"
            "🎯 **Получите вертикальное видео с черными полосами**\n\n"
            "📱 **Нажмите /start для начала**",
            reply_markup=get_main_keyboard()
        )

# Подключаем роутер
dp.include_router(router)

# === ЗАПУСК ===
async def on_startup():
    """Действия при запуске"""
    logger.info("=" * 60)
    logger.info("🚀 REELSMAKER ЗАПУСКАЕТСЯ")
    logger.info(f"📱 Поддержка: @{SUPPORT_USERNAME}")
    logger.info(f"⚙️ FFmpeg: {'✅' if ffmpeg_available else '❌'}")
    logger.info(f"💾 Воркеров: {executor._max_workers}")
    logger.info(f"📁 Temp dir: {TEMP_DIR}")
    logger.info("=" * 60)
    
    # Создаем структуру папок
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    if not os.path.exists(USERS_FILE):
        save_users({})
    
    # Очищаем старые временные файлы
    try:
        for item in TEMP_DIR.rglob("*"):
            if item.is_file():
                item.unlink()
        logger.info("✅ Временные файлы очищены")
    except:
        pass
    
    # Настраиваем вебхук если нужно
    webhook_url = os.getenv("RENDER_EXTERNAL_URL")
    if webhook_url:
        webhook_path = "/webhook"
        full_webhook_url = f"{webhook_url}{webhook_path}"
        
        try:
            await bot.set_webhook(
                url=full_webhook_url,
                secret_token=WEBHOOK_SECRET_TOKEN,
                drop_pending_updates=True
            )
            logger.info(f"✅ Вебхук установлен: {full_webhook_url}")
        except Exception as e:
            logger.error(f"❌ Ошибка вебхука: {e}")

async def on_shutdown():
    """Действия при остановке"""
    logger.info("🛑 Остановка бота...")
    try:
        await bot.delete_webhook()
    except:
        pass
    
    # Завершаем executor
    executor.shutdown(wait=False, cancel_futures=True)
    
    # Очищаем временные файлы
    try:
        for item in TEMP_DIR.rglob("*"):
            if item.is_file():
                item.unlink()
    except:
        pass

def start_webhook():
    """Запуск через вебхук"""
    app = web.Application()
    
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot
    )
    
    webhook_handler.register(app, path="/webhook")
    
    # Health check endpoint
    async def health_check(request):
        return web.Response(
            text=f"✅ ReelsMaker работает\n\n"
                 f"Поддержка: @{SUPPORT_USERNAME}\n"
                 f"Пользователей: {len(load_users())}\n"
                 f"Активных: {len(user_locks)}\n"
                 f"CPU: {psutil.cpu_percent()}%",
            status=200
        )
    
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    
    setup_application(app, dp, bot=bot)
    
    port = int(os.getenv("PORT", 10000))
    
    logger.info(f"🌐 Вебхук на порту: {port}")
    logger.info("✨ Бот готов к работе!")
    
    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
        access_log=None  # Отключаем логи aiohttp для экономии
    )

# === ГЛАВНАЯ ФУНКЦИЯ ===
if __name__ == "__main__":
    is_render = os.getenv("RENDER") == "true" or os.getenv("RENDER_EXTERNAL_URL")
    
    if is_render:
        logger.info(f"🚀 ЗАПУСК НА RENDER - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        start_webhook()
    else:
        from aiogram import executor as aiogram_executor
        logger.info("💻 Локальный запуск (polling)")
        aiogram_executor.start_polling(
            dp,
            skip_updates=True,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
            timeout=20,
            relax=0.1
        )
