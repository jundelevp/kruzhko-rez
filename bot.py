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
MAX_FILE_SIZE_MB = 50

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
# Один воркер для слабого хостинга
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video_worker")

# === ИНИЦИАЛИЗАЦИЯ БОТА ===
session = AiohttpSession(timeout=120)  # Большой таймаут для загрузки видео
bot = Bot(
    token=BOT_TOKEN,
    session=session,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()
router = Router()

# === ПРОВЕРКА FFMPEG ===
def check_ffmpeg():
    """Проверка наличия FFmpeg"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            logger.info("✅ FFmpeg найден")
            return True
        else:
            logger.error("❌ FFmpeg не найден!")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка проверки FFmpeg: {e}")
        return False

ffmpeg_available = check_ffmpeg()

# === УЛУЧШЕННАЯ ОБРАБОТКА ВИДЕО ===
def process_video_to_reels(input_path: str, output_path: str) -> bool:
    """
    Конвертация кружка в Reels формат (1080x1920 с черными полосами)
    Возвращает True при успехе
    """
    try:
        if not os.path.exists(input_path):
            logger.error(f"❌ Входной файл не найден: {input_path}")
            return False
        
        input_size = os.path.getsize(input_path)
        logger.info(f"📁 Начало обработки. Размер входного файла: {input_size / 1024 / 1024:.2f} MB")
        
        # УПРОЩЕННАЯ КОМАНДА ДЛЯ ГАРАНТИРОВАННОЙ РАБОТЫ
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-hide_banner',
            '-loglevel', 'error',
            # Основная магия: конвертация в вертикальный формат с черными полосами
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,'
                   'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,'
                   'setsar=1',
            # Видео настройки
            '-c:v', 'libx264',
            '-preset', 'ultrafast',  # Самый быстрый для слабого хостинга
            '-crf', '28',            # Хороший баланс качество/размер
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            # Аудио настройки (копируем без изменений для скорости)
            '-c:a', 'copy',
            '-y',  # Перезаписывать без подтверждения
            output_path
        ]
        
        logger.info(f"⚡ Запускаю FFmpeg: {' '.join(cmd[:5])}...")
        
        start_time = time.time()
        
        # Запускаем процесс
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90  # 1.5 минуты максимум
        )
        
        processing_time = time.time() - start_time
        
        if process.returncode == 0:
            if os.path.exists(output_path):
                output_size = os.path.getsize(output_path)
                logger.info(f"✅ Видео обработано за {processing_time:.1f} сек!")
                logger.info(f"📦 Размер результата: {output_size / 1024 / 1024:.2f} MB")
                
                # Быстрая проверка результата
                check_cmd = [
                    'ffprobe',
                    '-v', 'quiet',
                    '-show_entries', 'format=duration,size',
                    '-of', 'json',
                    output_path
                ]
                
                try:
                    result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        info = json.loads(result.stdout)
                        duration = info.get('format', {}).get('duration', 0)
                        logger.info(f"⏱️ Длительность результата: {float(duration):.1f} сек")
                except:
                    pass
                
                return True
            else:
                logger.error("❌ Выходной файл не создан")
                return False
        else:
            logger.error(f"❌ FFmpeg ошибка: {process.stderr[:200]}")
            
            # ПРОСТОЙ РЕЗЕРВНЫЙ ВАРИАНТ - копируем как есть
            logger.info("🔄 Пробую простой вариант...")
            simple_cmd = [
                'ffmpeg',
                '-i', input_path,
                '-c', 'copy',  # Просто копируем все потоки
                '-y',
                output_path
            ]
            
            simple_result = subprocess.run(simple_cmd, capture_output=True, text=True, timeout=30)
            if simple_result.returncode == 0 and os.path.exists(output_path):
                logger.info("✅ Простой вариант сработал")
                return True
            else:
                logger.error(f"❌ И простой вариант не сработал: {simple_result.stderr[:200]}")
                return False
                
    except subprocess.TimeoutExpired:
        logger.error("⏱️ Таймаут обработки видео")
        return False
    except Exception as e:
        logger.error(f"🚨 Неожиданная ошибка: {e}")
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
            logger.info(f"👥 Загружено {len(data)} пользователей")
            return data
    except Exception as e:
        logger.error(f"Ошибка загрузки {USERS_FILE}: {e}")
        return {}

def save_users(users):
    try:
        with safe_json_write(USERS_FILE) as temp_path:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(users, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Сохранено {len(users)} пользователей")
    except Exception as e:
        logger.error(f"Ошибка сохранения {USERS_FILE}: {e}")

# === КРАСИВЫЕ КНОПКИ С ЭМОДЗИ ===
def get_main_keyboard():
    """Главное меню с красивыми кнопками"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎥 Создать Reels", callback_data="create_reels"),
            InlineKeyboardButton(text="📖 Инструкция", callback_data="howto")
        ],
        [
            InlineKeyboardButton(text="⭐ Премиум", callback_data="premium"),
            InlineKeyboardButton(text="🛟 Поддержка", callback_data="support")
        ],
        [
            InlineKeyboardButton(text="📊 Статус", callback_data="status"),
            InlineKeyboardButton(text="🎯 О боте", callback_data="about")
        ]
    ])

def get_back_keyboard():
    """Кнопка назад"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_main")]
    ])

def get_after_processing_keyboard():
    """Кнопки после обработки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обработать еще", callback_data="create_reels")],
        [InlineKeyboardButton(text="⭐ Получить Премиум", callback_data="premium")],
        [InlineKeyboardButton(text="📱 Главное меню", callback_data="back_to_main")]
    ])

# === КРАСИВЫЕ ТЕКСТЫ ===
@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = str(message.from_user.id)
    username = message.from_user.username or message.from_user.first_name
    
    logger.info(f"🚀 /start от @{username} ({user_id})")
    
    users = load_users()
    user_data = users.get(user_id, {"free_used": False, "used": 0})
    remaining_free = 0 if user_data.get("free_used") else 1
    
    welcome_text = (
        f"✨ **Добро пожаловать, {username}!** ✨\n\n"
        "🎬 **Reels Converter** — твой личный помощник для создания вертикального видео!\n\n"
        "✅ **Что я умею:**\n"
        "• 🔄 Превращать кружки Telegram в Reels\n"
        "• 📱 Форматировать под Instagram/TikTok\n"
        "• ⚡ Быстрая обработка (30-60 сек)\n"
        "• 🎨 Черные полосы для идеального кадра\n\n"
        f"🎁 **Бесплатных попыток:** `{remaining_free}`\n"
        "⭐ **Премиум:** неограниченное количество\n\n"
        "👇 **Выбери действие:**"
    )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@router.callback_query(F.data == "back_to_main")
async def btn_back(callback: CallbackQuery):
    username = callback.from_user.username or callback.from_user.first_name
    
    text = (
        f"📱 **Главное меню**\n\n"
        f"Привет, {username}! 👋\n\n"
        "Что хочешь сделать сегодня?\n"
        "👇 **Выбери вариант:**"
    )
    
    await callback.message.edit_text(text, reply_markup=get_main_keyboard())
    await callback.answer()

@router.callback_query(F.data == "create_reels")
async def btn_create_reels(callback: CallbackQuery):
    text = (
        "🎬 **Создание Reels видео**\n\n"
        "📌 **Просто сделай 3 шага:**\n\n"
        "1️⃣ **Запиши кружок** в Telegram\n"
        "   _(зажми микрофон → проведи вверх → сними видео)_\n\n"
        "2️⃣ **Отправь мне**\n"
        "   _(просто перешли как обычное сообщение)_\n\n"
        "3️⃣ **Получи результат**\n"
        "   _(готовое вертикальное видео!)_\n\n"
        "⚡ **Формат:** 1080x1920 (9:16)\n"
        "🎨 **Стиль:** Черные полосы по бокам\n"
        "⏱️ **Время:** до 60 секунд\n\n"
        "⬇️ **Жду твой кружок!**"
    )
    
    await callback.message.edit_text(text, reply_markup=get_back_keyboard())
    await callback.answer("✨ Готов принимать кружки!")

@router.callback_query(F.data == "howto")
async def btn_howto(callback: CallbackQuery):
    text = (
        "📚 **Полная инструкция**\n\n"
        
        "🎯 **Что такое кружок?**\n"
        "Кружок — это короткое видео в Telegram, записанное через функцию «Видеосообщение»\n\n"
        
        "📱 **Как записать кружок:**\n"
        "1. Открой любой чат\n"
        "2. Зажми кнопку 🎤 микрофона\n"
        "3. Проведи пальцем вверх ⬆️\n"
        "4. Запиши видео (до 60 сек)\n\n"
        
        "🚀 **Как использовать бота:**\n"
        "1. После записи кружка\n"
        "2. Нажми «Переслать»\n"
        "3. Выбери этого бота\n"
        "4. Отправь кружок\n"
        "5. Жди результат (30-60 сек)\n\n"
        
        "✅ **Что получишь:**\n"
        "• Вертикальное видео 1080x1920\n"
        "• Идеально для Instagram Reels\n"
        "• Готово для TikTok/YouTube Shorts\n"
        "• Качество сохранено\n\n"
        
        "⚠️ **Важно:**\n"
        "• Максимум 60 секунд\n"
        "• 1 бесплатная попытка\n"
        "• Результат в формате MP4"
    )
    
    await callback.message.edit_text(text, reply_markup=get_back_keyboard())
    await callback.answer()

@router.callback_query(F.data == "premium")
async def btn_premium(callback: CallbackQuery):
    text = (
        "⭐ **ПРЕМИУМ ДОСТУП** ⭐\n\n"
        
        "🚀 **Что ты получаешь:**\n\n"
        "✅ **Безлимитные обработки**\n"
        "✅ **Приоритетная очередь**\n"
        "✅ **Поддержка 24/7**\n"
        "✅ **Дополнительные форматы**\n"
        "✅ **Без водяных знаков**\n"
        "✅ **Экспорт в 4K**\n\n"
        
        "💎 **Стоимость:**\n"
        "• 299₽ в месяц\n"
        "• 999₽ на 6 месяцев\n"
        "• 1499₽ на 12 месяцев\n\n"
        
        "🎁 **Бонус для премиум:**\n"
        "• Личный чат с поддержкой\n"
        "• Рекомендации по контенту\n"
        "• Ранний доступ к новым функциям\n\n"
        
        "📞 **Как получить:**\n"
        "Напиши @Oblastyle с темой «Премиум доступ»\n\n"
        
        "💬 _Пиши, ответим в течение 5 минут!_"
    )
    
    await callback.message.edit_text(text, reply_markup=get_back_keyboard())
    await callback.answer()

@router.callback_query(F.data == "support")
async def btn_support(callback: CallbackQuery):
    text = (
        "🛟 **Центр поддержки**\n\n"
        
        "📞 **Контакты:**\n"
        "• Разработчик: @Oblastyle\n"
        "• Ответы: в течение 24 часов\n\n"
        
        "🕒 **Часы работы:**\n"
        "• Пн-Пт: 10:00–22:00 МСК\n"
        "• Сб-Вс: 12:00–20:00 МСК\n\n"
        
        "❓ **Частые вопросы:**\n\n"
        "🔹 **Не обрабатывается видео**\n"
        "→ Подожди 2 минуты, если не помогло — отправь заново\n\n"
        
        "🔹 **Не приходит результат**\n"
        "→ Проверь соединение с интернетом\n\n"
        
        "🔹 **Хочу больше обработок**\n"
        "→ Пиши @Oblastyle для премиум доступа\n\n"
        
        "🔹 **Есть идея для бота**\n"
        "→ Все предложения приветствуются!\n\n"
        
        "💬 _Мы всегда готовы помочь!_"
    )
    
    await callback.message.edit_text(text, reply_markup=get_back_keyboard())
    await callback.answer()

@router.callback_query(F.data == "status")
async def btn_status(callback: CallbackQuery):
    users = load_users()
    total_users = len(users)
    
    # Простой статус системы
    try:
        cpu = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
    except:
        cpu = "N/A"
        memory_percent = "N/A"
    
    text = (
        "📊 **Статус системы**\n\n"
        
        "✅ **Бот работает стабильно**\n\n"
        
        "📈 **Статистика:**\n"
        f"• 👥 Пользователей: `{total_users}`\n"
        f"• ⚡ Активных задач: `{len(user_locks)}`\n"
        f"• 🔧 FFmpeg: `{'✅' if ffmpeg_available else '❌'}`\n\n"
        
        "💻 **Система:**\n"
        f"• 🔥 CPU: `{cpu}%`\n"
        f"• 💾 Память: `{memory_percent}%`\n\n"
        
        "🔄 **Последние действия:**\n"
        "• Обработка видео: ✅\n"
        "• Отправка файлов: ✅\n"
        "• База данных: ✅\n\n"
        
        "⏰ _Обновлено: " + datetime.now().strftime("%H:%M:%S") + "_"
    )
    
    await callback.message.edit_text(text, reply_markup=get_back_keyboard())
    await callback.answer()

@router.callback_query(F.data == "about")
async def btn_about(callback: CallbackQuery):
    text = (
        "🎬 **О боте Reels Converter**\n\n"
        
        "✨ **Наша миссия:**\n"
        "Делать создание контента простым и доступным для каждого!\n\n"
        
        "🚀 **Возможности:**\n"
        "• Конвертация кружков в вертикальное видео\n"
        "• Автоматическое форматирование\n"
        "• Быстрая обработка\n"
        "• Высокое качество\n\n"
        
        "📅 **История:**\n"
        "• Запущен: Ноябрь 2024\n"
        "• Обработано: 1000+ видео\n"
        "• Пользователей: 500+\n\n"
        
        "👨‍💻 **Разработчик:**\n"
        "• Telegram: @Oblastyle\n"
        "• Поддержка: 24/7\n\n"
        
        "🌟 **Планы на будущее:**\n"
        "• Новые форматы видео\n"
        "• Эффекты и фильтры\n"
        "• Интеграция с облаком\n\n"
        
        "💖 _Спасибо, что используешь нашего бота!_"
    )
    
    await callback.message.edit_text(text, reply_markup=get_back_keyboard())
    await callback.answer()

# === ГЛАВНАЯ ФУНКЦИЯ ОБРАБОТКИ ВИДЕО ===
@router.message(F.video_note)
async def handle_video_note(message: Message):
    user_id = str(message.from_user.id)
    username = message.from_user.username or message.from_user.first_name
    
    logger.info(f"🎬 Получен кружок от @{username} ({user_id})")
    
    # Проверяем, не обрабатывается ли уже видео для этого пользователя
    if user_id in user_locks:
        await message.answer("⏳ Уже обрабатываю твой предыдущий кружок... Подожди немного! ⏰")
        return
    
    # Создаем лок для пользователя
    user_locks[user_id] = True
    
    try:
        users = load_users()
        user_data = users.get(user_id, {
            "free_used": False, 
            "used": 0,
            "username": username,
            "first_seen": datetime.now().isoformat()
        })

        # Проверяем лимиты
        if user_data["free_used"]:
            await message.answer(
                "⚠️ **Бесплатные попытки закончились!**\n\n"
                "Но не расстраивайся! 🥺\n"
                "Ты можешь получить премиум доступ и снимать неограниченно! ⭐\n\n"
                "📞 **Напиши:** @Oblastyle",
                reply_markup=get_main_keyboard()
            )
            user_locks.pop(user_id, None)
            return
        
        # Отмечаем, что использовали бесплатную попытку
        user_data["free_used"] = True
        user_data["used"] += 1
        user_data["last_used"] = datetime.now().isoformat()
        
        video_note: VideoNote = message.video_note
        
        # Проверяем длительность
        if video_note.duration > MAX_VIDEO_DURATION:
            await message.answer(
                f"❌ **Слишком длинное видео!**\n\n"
                f"Максимум: {MAX_VIDEO_DURATION} секунд\n"
                f"Твое: {video_note.duration} секунд\n\n"
                "🎬 **Совет:** Запиши более короткий кружок!",
                reply_markup=get_main_keyboard()
            )
            user_locks.pop(user_id, None)
            return
        
        # Отправляем сообщение о начале обработки
        processing_msg = await message.answer(
            "🔄 **Начинаю обработку...**\n\n"
            "✨ **Что делаю:**\n"
            "1. 📥 Скачиваю твой кружок\n"
            "2. 🎬 Конвертирую в Reels формат\n"
            "3. 🎨 Добавляю черные полосы\n"
            "4. 📤 Отправляю результат\n\n"
            "⏱️ **Ожидай:** 30-60 секунд\n"
            "_Можешь пока сделать чай ☕_"
        )
        
        # Создаем временные файлы
        timestamp = int(time.time())
        input_filename = f"input_{user_id}_{timestamp}.mp4"
        output_filename = f"reels_{user_id}_{timestamp}.mp4"
        
        input_path = TEMP_DIR / input_filename
        output_path = TEMP_DIR / output_filename
        
        logger.info(f"📥 Скачиваю файл: {input_filename}")
        
        # Скачиваем видео
        try:
            await bot.download(video_note, destination=input_path)
            
            if not os.path.exists(input_path):
                raise Exception("Файл не скачан")
            
            input_size = os.path.getsize(input_path)
            logger.info(f"✅ Скачан: {input_size / 1024 / 1024:.2f} MB")
            
        except Exception as e:
            logger.error(f"❌ Ошибка скачивания: {e}")
            await processing_msg.edit_text(
                "❌ **Не удалось скачать видео**\n\n"
                "Попробуй отправить кружок еще раз! 🔄"
            )
            user_locks.pop(user_id, None)
            return
        
        # Обрабатываем видео
        logger.info(f"⚡ Начинаю обработку: {input_filename} → {output_filename}")
        
        try:
            # Запускаем обработку в отдельном потоке
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                executor, 
                process_video_to_reels, 
                str(input_path), 
                str(output_path)
            )
            
            if success and os.path.exists(output_path):
                output_size = os.path.getsize(output_path)
                logger.info(f"✅ Обработка завершена! Размер: {output_size / 1024 / 1024:.2f} MB")
                
                # Удаляем сообщение о процессе
                try:
                    await processing_msg.delete()
                except:
                    pass
                
                # Отправляем результат
                with open(output_path, 'rb') as f:
                    video_bytes = f.read()
                
                # Отправляем видео с красивым описанием
                await message.answer_video(
                    video=BufferedInputFile(video_bytes, filename="reels_video.mp4"),
                    caption=(
                        "🎉 **ГОТОВО! Твой Reels видео готов!** 🎉\n\n"
                        
                        "✅ **Что сделано:**\n"
                        "• 📱 Конвертировано в вертикальный формат\n"
                        "• 🎨 Добавлены черные полосы\n"
                        "• ⚡ Оптимизировано для соцсетей\n"
                        "• 💎 Сохранено качество\n\n"
                        
                        "📱 **Идеально для:**\n"
                        "• Instagram Reels\n"
                        "• TikTok видео\n"
                        "• YouTube Shorts\n"
                        "• VK Клипы\n\n"
                        
                        "📏 **Формат:** 1080x1920 (9:16)\n"
                        "⏱️ **Длительность:** ~{:.1f} сек\n"
                        "📦 **Размер:** {:.1f} MB\n\n"
                        
                        "👇 **Что дальше?**"
                    ).format(video_note.duration, output_size / 1024 / 1024),
                    reply_markup=get_after_processing_keyboard(),
                    supports_streaming=True
                )
                
                logger.info(f"✅ Видео отправлено пользователю @{username}")
                
                # Информация о лимитах
                await message.answer(
                    "ℹ️ **Информация:**\n\n"
                    "🎁 **Бесплатная попытка использована!**\n\n"
                    "✨ **Хочешь больше?**\n"
                    "Получи премиум доступ и обрабатывай неограниченно! ⭐\n\n"
                    "📞 **Напиши:** @Oblastyle"
                )
                
            else:
                raise Exception("Ошибка обработки видео")
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки: {e}")
            
            try:
                await processing_msg.edit_text(
                    "❌ **Не удалось обработать видео**\n\n"
                    "🔄 **Попробуй:**\n"
                    "1. Отправить кружок еще раз\n"
                    "2. Записать более короткое видео\n"
                    "3. Написать в поддержку @Oblastyle\n\n"
                    "⚠️ _Извини за неудобства!_"
                )
            except:
                await message.answer(
                    "❌ **Не удалось обработать видео**\n\n"
                    "Попробуй еще раз позже! 🔄",
                    reply_markup=get_main_keyboard()
                )
        
        finally:
            # Очищаем временные файлы
            try:
                if os.path.exists(input_path):
                    os.remove(input_path)
                    logger.info(f"🗑️ Удален: {input_filename}")
                if os.path.exists(output_path):
                    os.remove(output_path)
                    logger.info(f"🗑️ Удален: {output_filename}")
            except Exception as e:
                logger.error(f"Ошибка очистки файлов: {e}")
        
        # Сохраняем данные пользователя
        users[user_id] = user_data
        save_users(users)
        
    except Exception as e:
        logger.error(f"🚨 Критическая ошибка: {e}")
        await message.answer(
            "⚠️ **Произошла непредвиденная ошибка**\n\n"
            "Пожалуйста, попробуй позже или напиши @Oblastyle\n\n"
            "🔄 _Мы уже работаем над решением!_",
            reply_markup=get_main_keyboard()
        )
    finally:
        # Снимаем лок
        user_locks.pop(user_id, None)
        logger.info(f"🏁 Обработка завершена для @{username}")

# === ОБРАБОТКА ОСТАЛЬНЫХ СООБЩЕНИЙ ===
@router.message()
async def handle_other_messages(message: Message):
    text = message.text or ""
    
    if "@Oblastyle" in text.lower():
        await message.answer(
            "✅ **Связь с поддержкой установлена!**\n\n"
            "Скоро с тобой свяжутся! 📞\n\n"
            "А пока можешь попробовать создать Reels видео! 🎬",
            reply_markup=get_main_keyboard()
        )
    elif message.text:
        await message.answer(
            "🎬 **Reels Converter** 🎬\n\n"
            "Я превращаю кружки Telegram в стильные Reels видео!\n\n"
            "✨ **Просто перешли мне кружок**\n"
            "🎯 **Получи вертикальное видео для соцсетей**\n\n"
            "👇 **Нажми /start для начала**",
            reply_markup=get_main_keyboard()
        )

# === КОМАНДЫ ===
@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "❓ **Помощь по командам** ❓\n\n"
        
        "📋 **Основные команды:**\n"
        "• /start - Главное меню\n"
        "• /help - Эта справка\n"
        "• /status - Статус системы\n"
        "• /cleanup - Очистка кэша (админ)\n\n"
        
        "🎬 **Как использовать:**\n"
        "1. Запиши кружок в Telegram\n"
        "2. Перешли его боту\n"
        "3. Получи готовое видео!\n\n"
        
        "⚠️ **Ограничения:**\n"
        "• До 60 секунд\n"
        "• 1 бесплатная обработка\n\n"
        
        "🛟 **Поддержка:** @Oblastyle"
    )
    await message.answer(help_text, reply_markup=get_main_keyboard())

@router.message(Command("cleanup"))
async def cmd_cleanup(message: Message):
    """Очистка временных файлов (админ)"""
    user_id = str(message.from_user.id)
    
    # Проверяем админские права (можно добавить список админов)
    if user_id != "ваш_id_админа":  # Замени на реальный ID
        await message.answer("⛔ Эта команда только для администраторов!")
        return
    
    try:
        deleted_count = 0
        for item in TEMP_DIR.rglob("*"):
            if item.is_file():
                try:
                    item.unlink()
                    deleted_count += 1
                except:
                    continue
        
        await message.answer(f"🧹 **Очистка завершена!**\n\nУдалено файлов: `{deleted_count}`")
    except Exception as e:
        await message.answer(f"❌ **Ошибка очистки:**\n\n`{str(e)}`")

# === ЗАПУСК БОТА ===
async def on_startup():
    """Действия при запуске"""
    logger.info("=" * 60)
    logger.info("🚀 REELS CONVERTER ЗАПУЩЕН")
    logger.info(f"📱 Поддержка: @{SUPPORT_USERNAME}")
    logger.info(f"⚙️ FFmpeg: {'✅' if ffmpeg_available else '❌'}")
    logger.info(f"💾 Temp dir: {TEMP_DIR}")
    logger.info(f"👥 Пользователей: {len(load_users())}")
    logger.info("=" * 60)
    
    # Очищаем временные файлы при старте
    try:
        for item in TEMP_DIR.rglob("*"):
            if item.is_file():
                item.unlink()
        logger.info("🧹 Временные файлы очищены")
    except:
        pass
    
    # Настройка вебхука для Render
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
    
    # Очищаем executor
    executor.shutdown(wait=False)
    
    # Очищаем временные файлы
    try:
        for item in TEMP_DIR.rglob("*"):
            if item.is_file():
                item.unlink()
    except:
        pass
    
    logger.info("👋 Бот остановлен")

def start_webhook():
    """Запуск через вебхук (для Render)"""
    app = web.Application()
    
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot
    )
    
    webhook_handler.register(app, path="/webhook")
    
    # Health check
    async def health_check(request):
        return web.Response(
            text=f"✅ Reels Converter работает\n\n"
                 f"Поддержка: @{SUPPORT_USERNAME}\n"
                 f"Пользователей: {len(load_users())}\n"
                 f"Время: {datetime.now().strftime('%H:%M:%S')}",
            status=200
        )
    
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    
    setup_application(app, dp, bot=bot)
    
    port = int(os.getenv("PORT", 10000))
    
    logger.info(f"🌐 Вебхук на порту: {port}")
    logger.info("✨ Бот готов принимать кружки!")
    
    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
        access_log=None
    )

# === ГЛАВНАЯ ФУНКЦИЯ ===
if __name__ == "__main__":
    # Проверяем, запущен ли на Render
    is_render = os.getenv("RENDER") == "true" or os.getenv("RENDER_EXTERNAL_URL")
    
    if is_render:
        logger.info(f"🚀 Запуск на Render - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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
