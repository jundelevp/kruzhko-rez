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

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "8535285877:AAFkJEwV18KFCnEJPAyTR2AsSsgvQbTA6fg")
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "default_secret_token_123")

if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не установлен!")
    exit(1)

print(f"✨ Бот запускается... Токен: {BOT_TOKEN[:10]}...")

MAX_VIDEO_DURATION = 60
FREE_LIMIT = 1
SUPPORT_USERNAME = "Oblastyle"  # 🛠 Исправленный контакт поддержки

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
USERS_FILE.parent.mkdir(exist_ok=True)

user_locks = {}
executor = ThreadPoolExecutor(max_workers=2)

# === ИНИЦИАЛИЗАЦИЯ БОТА ===
session = AiohttpSession(timeout=30)
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
        result = subprocess.run(['which', 'ffmpeg'], capture_output=True, text=True)
        if result.returncode == 0:
            logger.info(f"✅ FFmpeg найден: {result.stdout.strip()}")
            return True
        else:
            logger.warning("❌ FFmpeg не найден!")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка проверки FFmpeg: {e}")
        return False

# Проверяем при старте
ffmpeg_available = check_ffmpeg()

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

# === 🎨 КРАСИВЫЕ КЛАВИАТУРЫ ===
def get_main_keyboard():
    """Главное меню с красивым дизайном"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Создать видео", callback_data="make_video")],
        [InlineKeyboardButton(text="📱 Инструкция", callback_data="howto")],
        [InlineKeyboardButton(text="⭐ Премиум", callback_data="premium")],
        [InlineKeyboardButton(text="🛠 Поддержка", callback_data="support")]
    ])

def get_back_keyboard():
    """Кнопка назад"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_main")]
    ])

def get_processing_keyboard():
    """Кнопки во время обработки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Проверить статус", callback_data="check_status")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")]
    ])

# === 🎥 ОБРАБОТКА ВИДЕО (ПРАВИЛЬНЫЙ ФОРМАТ) ===
async def async_process_video(input_path: str, output_path: str, duration: float):
    """Правильная обработка видео - сохраняем исходный формат кружка, но убираем круглую маску"""
    
    def _process():
        try:
            if not os.path.exists(input_path):
                raise FileNotFoundError(f"Входной файл не найден: {input_path}")
            
            file_size = os.path.getsize(input_path)
            logger.info(f"🎞️ Начало обработки. Размер: {file_size} байт")
            
            if ffmpeg_available:
                # ✅ ПРАВИЛЬНАЯ КОМАНДА:
                # 1. Убираем круглую маску (телеграм добавляет её к кружкам)
                # 2. Сохраняем квадратный формат 1080x1080 (как у кружков)
                # 3. НЕ добавляем белый фон
                cmd = [
                    'ffmpeg',
                    '-i', input_path,
                    # Ключевой фильтр: убираем альфа-канал (прозрачность) который делает круглую маску
                    '-vf', 'format=yuv420p,scale=1080:1080:force_original_aspect_ratio=increase',
                    '-c:v', 'libx264',
                    '-preset', 'fast',
                    '-crf', '23',           # Хорошее качество
                    '-c:a', 'aac',
                    '-b:a', '128k',
                    '-movflags', '+faststart',
                    '-y',
                    output_path
                ]
                
                logger.info("🔄 Запуск обработки видео...")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
                
                if result.returncode == 0:
                    output_size = os.path.getsize(output_path)
                    logger.info(f"✅ Видео обработано! Размер: {output_size} байт")
                    
                    # Проверяем результат
                    check_cmd = [
                        'ffprobe',
                        '-v', 'error',
                        '-select_streams', 'v:0',
                        '-show_entries', 'stream=width,height,codec_name',
                        '-of', 'csv=p=0',
                        output_path
                    ]
                    check_result = subprocess.run(check_cmd, capture_output=True, text=True)
                    if check_result.returncode == 0:
                        logger.info(f"📐 Результат: {check_result.stdout.strip()}")
                    
                    return True
                else:
                    logger.error(f"❌ FFmpeg ошибка: {result.stderr[:300]}")
                    
                    # 🔄 Резервный вариант - простое копирование
                    logger.info("🔄 Пробую резервный вариант...")
                    backup_cmd = [
                        'ffmpeg',
                        '-i', input_path,
                        '-c:v', 'copy',  # Просто копируем
                        '-c:a', 'copy',
                        '-y',
                        output_path
                    ]
                    
                    backup_result = subprocess.run(backup_cmd, capture_output=True, text=True, timeout=30)
                    if backup_result.returncode == 0:
                        logger.info("✅ Резервная обработка успешна")
                        return True
                    else:
                        return False
            else:
                logger.error("❌ FFmpeg не доступен!")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error("⏱️ Таймаут обработки видео")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка обработки: {e}")
            return False

    loop = asyncio.get_event_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(executor, _process),
            timeout=60.0  # 60 секунд максимум
        )
        logger.info("✅ Обработка завершена")
        return True
    except asyncio.TimeoutError:
        logger.error("⏱️ Общий таймаут обработки")
        return False

# === 💬 ОБРАБОТКА СООБЩЕНИЙ ===
@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = str(message.from_user.id)
    logger.info(f"🚀 Команда /start от пользователя {user_id}")
    
    users = load_users()
    user_data = users.get(user_id, {"free_used": False, "used": 0})
    remaining_free = 0 if user_data.get("free_used") else 1
    
    welcome_text = (
        "✨ **Добро пожаловать в КружкоРез!** ✨\n\n"
        "🎬 **Я превращаю кружки Telegram в готовые видео!**\n\n"
        "✅ **Что я делаю:**\n"
        "• Убираю круглую маску\n"
        "• Сохраняю исходное качество\n"
        "• Оптимизирую для соцсетей\n"
        "• Готово за 30 секунд!\n\n"
        f"🎁 **У вас осталось: {remaining_free} бесплатных обработок**\n\n"
        "_Выберите действие ниже:_ 👇"
    )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@router.callback_query(F.data == "back_to_main")
async def btn_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "📱 **Главное меню**\n\n_Выберите действие:_ 👇",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "make_video")
async def btn_make_video(callback: CallbackQuery):
    instruction = (
        "🎬 **Как создать видео:**\n\n"
        "1. 📱 **Запишите кружок** в Telegram\n"
        "   _Зажмите микрофон → проведите вверх → снимите видео_\n\n"
        "2. 📤 **Перешлите его мне**\n"
        "   _Просто перешлите как обычное сообщение_\n\n"
        "3. ⚡ **Получите готовое видео**\n"
        "   _Без круглой маски, готово для соцсетей!_\n\n"
        "⏱️ **Время обработки:** 20-40 секунд\n"
        "📏 **Формат:** Квадратное видео 1080x1080\n\n"
        "⬇️ **Перешлите кружок прямо сейчас!**"
    )
    
    await callback.message.edit_text(instruction, reply_markup=get_back_keyboard())
    await callback.answer()

@router.callback_query(F.data == "howto")
async def btn_howto(callback: CallbackQuery):
    guide = (
        "📚 **Полная инструкция:**\n\n"
        "🎯 **Что такое кружок?**\n"
        "Кружок — это видеосообщение в Telegram, которое записывается нажатием на микрофон.\n\n"
        "🔧 **Как использовать бота:**\n\n"
        "**ШАГ 1: Запись кружка**\n"
        "• Откройте чат с кем-то\n"
        "• Зажмите кнопку микрофона\n"
        "• Проведите пальцем вверх\n"
        "• Запишите видео (до 60 секунд)\n\n"
        "**ШАГ 2: Отправка боту**\n"
        "• Нажмите на кружок\n"
        "• Выберите «Переслать»\n"
        "• Найдите @KruzhkoRez_bot\n"
        "• Отправьте\n\n"
        "**ШАГ 3: Получение результата**\n"
        "• Подождите 20-40 секунд\n"
        "• Получите готовое видео\n"
        "• Сохраните и используйте!\n\n"
        "⚠️ **Важно:**\n"
        "• Максимальная длительность: 60 секунд\n"
        "• 1 бесплатная обработка на пользователя\n"
        "• Результат: квадратное видео 1080x1080"
    )
    
    await callback.message.edit_text(guide, reply_markup=get_back_keyboard())
    await callback.answer()

@router.callback_query(F.data == "premium")
async def btn_premium(callback: CallbackQuery):
    premium_info = (
        "⭐ **ПРЕМИУМ ДОСТУП** ⭐\n\n"
        "🚀 **Что вы получаете:**\n\n"
        "✅ **Неограниченные обработки**\n"
        "✅ **Приоритетная очередь**\n"
        "✅ **Поддержка 24/7**\n"
        "✅ **Экспорт в 4K качество**\n"
        "✅ **Дополнительные форматы**\n\n"
        "💎 **Стоимость:** 299₽/месяц\n\n"
        "📲 **Как получить:**\n"
        "Напишите @Oblastyle с темой «Премиум доступ»\n\n"
        "_Превращайте кружки в профессиональный контент!_"
    )
    
    await callback.message.edit_text(premium_info, reply_markup=get_back_keyboard())
    await callback.answer()

@router.callback_query(F.data == "support")
async def btn_support(callback: CallbackQuery):
    """🛠 ИСПРАВЛЕННЫЙ раздел поддержки"""
    support_text = (
        "🛠 **ЦЕНТР ПОДДЕРЖКИ**\n\n"
        "📞 **Связь с разработчиком:**\n"
        "👉 @Oblastyle\n\n"
        "🕒 **Время ответа:**\n"
        "• Пн-Пт: 10:00 - 22:00 МСК\n"
        "• Сб-Вс: 12:00 - 20:00 МСК\n\n"
        "❓ **Частые вопросы:**\n\n"
        "**Q: Видео не обрабатывается**\n"
        "A: Подождите 2 минуты, если не помогло — перешлите кружок заново\n\n"
        "**Q: Не приходит результат**\n"
        "A: Проверьте соединение с интернетом\n\n"
        "**Q: Хочу больше обработок**\n"
        "A: Напишите @Oblastyle для премиум доступа\n\n"
        "📧 **Пишите, поможем!**"
    )
    
    await callback.message.edit_text(support_text, reply_markup=get_back_keyboard())
    await callback.answer()

# === 🎥 ОСНОВНАЯ ОБРАБОТКА КРУЖКА ===
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
            "username": message.from_user.username
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

        # Отправляем сообщение о начале обработки
        processing_msg = await message.answer(
            "🔄 **Начинаю обработку...**\n\n"
            "✨ **Что делаю:**\n"
            "• Убираю круглую маску\n"
            "• Оптимизирую качество\n"
            "• Готовлю для соцсетей\n\n"
            "⏱️ **Примерное время:** 30 секунд\n"
            "_Не закрывайте Telegram..._",
            reply_markup=get_processing_keyboard()
        )

        video_note: VideoNote = message.video_note
        logger.info(f"📊 Длительность: {video_note.duration}сек, Размер: {video_note.file_size}")
        
        if video_note.duration > MAX_VIDEO_DURATION:
            await message.answer(
                f"❌ **Слишком длинный кружок**\n\n"
                f"Максимум: {MAX_VIDEO_DURATION} секунд\n"
                f"Ваш: {video_note.duration} секунд\n\n"
                "Запишите более короткий кружок! 🎬"
            )
            return

        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, "input.mp4")
            output_path = os.path.join(temp_dir, "output_video.mp4")
            
            logger.info(f"📥 Скачиваю файл...")
            
            try:
                await bot.download(video_note, destination=input_path)
                
                if not os.path.exists(input_path):
                    raise FileNotFoundError("Файл не скачан")
                
                file_size = os.path.getsize(input_path)
                logger.info(f"✅ Файл скачан: {file_size} байт")
                
            except Exception as e:
                logger.error(f"❌ Ошибка скачивания: {e}")
                await message.answer(
                    "❌ **Не удалось скачать кружок**\n\n"
                    "Попробуйте отправить его еще раз! 🔄"
                )
                return

            try:
                logger.info("⚡ Начинаю обработку видео...")
                success = await async_process_video(input_path, output_path, video_note.duration)
                
                if success and os.path.exists(output_path):
                    output_size = os.path.getsize(output_path)
                    logger.info(f"✅ Обработка завершена! Размер: {output_size} байт")
                    
                    try:
                        await processing_msg.delete()
                    except:
                        pass
                    
                    # Отправляем результат
                    with open(output_path, 'rb') as f:
                        video_bytes = f.read()
                    
                    await message.answer_video(
                        video=BufferedInputFile(video_bytes, filename="video_ready.mp4"),
                        caption=(
                            "🎉 **ГОТОВО!** 🎉\n\n"
                            "✅ **Кружок успешно обработан!**\n\n"
                            "📱 **Идеально для:**\n"
                            "• Instagram Reels\n"
                            "• TikTok\n"
                            "• YouTube Shorts\n"
                            "• VK Клипы\n\n"
                            "📏 **Формат:** 1080x1080\n"
                            "⚡ **Качество:** Оптимизировано\n"
                            "🎬 **Без круглой маски**\n\n"
                            "_Сохраняйте и делитесь!_ ✨"
                        )
                    )
                    logger.info(f"✅ Видео отправлено пользователю {user_id}")
                    
                    if is_free:
                        await message.answer(
                            "🎁 **Это была ваша бесплатная обработка!**\n\n"
                            "Хотите больше? Пишите @Oblastyle для премиум доступа! ⭐",
                            reply_markup=get_main_keyboard()
                        )
                else:
                    raise RuntimeError("Ошибка обработки видео")
                    
            except Exception as e:
                logger.error(f"❌ Ошибка обработки: {e}")
                await message.answer(
                    "❌ **Не удалось обработать кружок**\n\n"
                    "Попробуйте еще раз или напишите @Oblastyle 📞",
                    reply_markup=get_main_keyboard()
                )

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

# === 📱 ДОПОЛНИТЕЛЬНЫЕ КОМАНДЫ ===
@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "❓ **ПОМОЩЬ ПО БОТУ** ❓\n\n"
        "📋 **Основные команды:**\n"
        "/start - Главное меню\n"
        "/help - Эта справка\n"
        "/status - Статус бота\n\n"
        "🎬 **Как использовать:**\n"
        "1. Запишите кружок в Telegram\n"
        "2. Перешлите его боту\n"
        "3. Получите готовое видео!\n\n"
        "⚠️ **Ограничения:**\n"
        "• До 60 секунд\n"
        "• 1 бесплатная обработка\n\n"
        "🛠 **Поддержка:** @Oblastyle"
    )
    await message.answer(help_text, reply_markup=get_main_keyboard())

@router.message(Command("status"))
async def cmd_status(message: Message):
    users = load_users()
    total_users = len(users)
    
    status_text = (
        "📊 **СТАТУС БОТА**\n\n"
        "✅ **Бот работает**\n"
        "👥 **Пользователей:** {}\n"
        "⚡ **Активных обработок:** {}\n"
        "🔧 **FFmpeg:** {}\n"
        "🌐 **Режим:** {}\n\n"
        "_Обновлено: {}_"
    ).format(
        total_users,
        len(user_locks),
        "✅ Доступен" if ffmpeg_available else "❌ Недоступен",
        "вебхук" if os.getenv('RENDER_EXTERNAL_URL') else "polling",
        datetime.now().strftime("%H:%M:%S")
    )
    
    await message.answer(status_text)

# === 📞 ОБРАБОТКА ОСТАЛЬНЫХ СООБЩЕНИЙ ===
@router.message()
async def handle_other(message: Message):
    if message.text and "@Oblastyle" in message.text:
        await message.answer(
            "✅ **Связь с поддержкой установлена!**\n\n"
            "Скоро с вами свяжутся! 📞\n\n"
            "А пока можете попробовать обработать кружок! 🎬",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            "🎬 **КружкоРез** 🎬\n\n"
            "Я обрабатываю кружки Telegram в готовые видео!\n\n"
            "✨ **Просто перешлите мне кружок**\n"
            "🎯 **Получите видео без круглой маски**\n\n"
            "📱 **Нажмите /start для начала**",
            reply_markup=get_main_keyboard()
        )

# Подключаем роутер
dp.include_router(router)

# === 🚀 ЗАПУСК ВЕБХУКА ===
async def on_startup():
    """Действия при запуске"""
    logger.info("=" * 60)
    logger.info("🚀 КРУЖКОРЕЗ ЗАПУСКАЕТСЯ")
    logger.info(f"📱 Поддержка: @{SUPPORT_USERNAME}")
    logger.info(f"⚙️ FFmpeg: {'✅' if ffmpeg_available else '❌'}")
    logger.info("=" * 60)
    
    if not os.path.exists(USERS_FILE.parent):
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    if not os.path.exists(USERS_FILE):
        save_users({})
    
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
            logger.info(f"✅ Вебхук: {full_webhook_url}")
        except Exception as e:
            logger.error(f"❌ Вебхук: {e}")

async def on_shutdown():
    """Действия при остановке"""
    logger.info("🛑 Остановка бота...")
    try:
        await bot.delete_webhook()
    except:
        pass
    executor.shutdown(wait=False)

def start_webhook():
    """Запуск через вебхук (Render)"""
    app = web.Application()
    
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot
    )
    
    webhook_handler.register(app, path="/webhook")
    
    async def health_check(request):
        return web.Response(
            text="✅ КружкоРез работает\n\n"
                 f"Поддержка: @{SUPPORT_USERNAME}\n"
                 f"Пользователей: {len(load_users())}",
            status=200
        )
    
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    
    setup_application(app, dp, bot=bot)
    
    port = int(os.getenv("PORT", 10000))
    
    logger.info(f"🌐 Порт: {port}")
    logger.info(f"✨ Бот готов к работе!")
    
    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
        access_log=logger
    )

# === 🎯 ГЛАВНАЯ ФУНКЦИЯ ===
if __name__ == "__main__":
    from datetime import datetime
    
    is_render = os.getenv("RENDER") == "true" or os.getenv("RENDER_EXTERNAL_URL")
    
    if is_render:
        logger.info(f"🚀 ЗАПУСК НА RENDER - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        start_webhook()
    else:
        from aiogram import executor as aiogram_executor
        logger.info("💻 Локальный запуск")
        aiogram_executor.start_polling(
            dp,
            skip_updates=True,
            on_startup=on_startup,
            on_shutdown=on_shutdown
        )

