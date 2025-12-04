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

print(f"🔥 Бот стартует! Токен: {BOT_TOKEN[:10]}...")

MAX_VIDEO_DURATION = 60
FREE_LIMIT = 1
PREMIUM_QUOTA = 15
PRICE = 199
SUPPORT_USERNAME = "@kruzhkorez_support"

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
            
            # Проверяем кодек libx264
            version_result = subprocess.run(['ffmpeg', '-codecs'], capture_output=True, text=True)
            if 'libx264' in version_result.stdout:
                logger.info("✅ Кодек libx264 доступен")
            else:
                logger.warning("⚠️ Кодек libx264 не найден")
                
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

# === КЛАВИАТУРА ===
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎥 Сделать видео", callback_data="make_video")],
        [InlineKeyboardButton(text="ℹ️ Как пользоваться", callback_data="howto")],
        [InlineKeyboardButton(text="🛠 Поддержка", callback_data="support")]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])

# === ОБРАБОТКА ВИДЕО (ВЕРТИКАЛЬНЫЙ ФОРМАТ) ===
async def async_process_video(input_path: str, output_path: str, duration: float):
    """Обработка видео в вертикальный формат для Reels/Shorts (1080x1920)"""
    
    def _process():
        try:
            if not os.path.exists(input_path):
                raise FileNotFoundError(f"Входной файл не найден: {input_path}")
            
            file_size = os.path.getsize(input_path)
            logger.info(f"⚡ Начало обработки видео в вертикальный формат. Размер: {file_size} байт")
            
            # РЕАЛЬНАЯ обработка для вертикального видео 1080x1920
            if ffmpeg_available:
                # Команда для создания вертикального видео с черными полосами
                cmd = [
                    'ffmpeg',
                    '-i', input_path,
                    # Масштабируем с сохранением пропорций, добавляем черные полосы
                    '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1',
                    '-c:v', 'libx264',      # Кодек H.264
                    '-preset', 'fast',      # Баланс скорость/качество
                    '-crf', '24',           # Качество (23-28 нормально)
                    '-c:a', 'aac',          # Аудио кодек
                    '-b:a', '128k',         # Битрейт аудио
                    '-movflags', '+faststart',  # Для быстрого старта
                    '-y',                   # Перезаписать
                    output_path
                ]
                
                logger.info("🎬 Запуск обработки видео в вертикальный формат 1080x1920")
                logger.info(f"📏 Команда FFmpeg: {' '.join(cmd)}")
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)  # 2 минуты на обработку
                
                if result.returncode == 0:
                    output_size = os.path.getsize(output_path)
                    logger.info(f"✅ Видео успешно обработано! Размер: {output_size} байт")
                    
                    # Проверяем разрешение выходного файла
                    probe_cmd = [
                        'ffprobe',
                        '-v', 'error',
                        '-select_streams', 'v:0',
                        '-show_entries', 'stream=width,height',
                        '-of', 'csv=p=0',
                        output_path
                    ]
                    
                    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
                    if probe_result.returncode == 0:
                        resolution = probe_result.stdout.strip()
                        logger.info(f"📐 Разрешение выходного видео: {resolution}")
                    
                    return True
                else:
                    logger.error(f"❌ FFmpeg ошибка: {result.stderr[:500]}")
                    
                    # Попробуем упрощенный вариант если первый не сработал
                    logger.info("🔄 Пробую упрощенную обработку...")
                    simple_cmd = [
                        'ffmpeg',
                        '-i', input_path,
                        '-vf', 'scale=1080:1920,setsar=1',  # Простое масштабирование
                        '-c:v', 'libx264',
                        '-preset', 'ultrafast',  # Максимально быстро
                        '-c:a', 'copy',          # Копируем аудио без изменений
                        '-y',
                        output_path
                    ]
                    
                    simple_result = subprocess.run(simple_cmd, capture_output=True, text=True, timeout=60)
                    if simple_result.returncode == 0:
                        logger.info("✅ Упрощенная обработка успешна")
                        return True
                    else:
                        logger.error(f"❌ Упрощенная обработка тоже не удалась: {simple_result.stderr[:500]}")
                        return False
            else:
                logger.error("❌ FFmpeg не доступен! Невозможно обработать видео")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error("❌ Таймаут обработки видео (120 сек)")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка обработки видео: {e}")
            return False

    loop = asyncio.get_event_loop()
    try:
        # Даем достаточно времени на обработку - 150 секунд
        await asyncio.wait_for(
            loop.run_in_executor(executor, _process),
            timeout=150.0
        )
        logger.info("✅ Обработка видео завершена успешно")
        return True
    except asyncio.TimeoutError:
        logger.error("❌ Общий таймаут обработки (150 сек)")
        return False
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка в async_process_video: {e}")
        return False

# === ОБРАБОТКА СООБЩЕНИЙ ===
@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = str(message.from_user.id)
    logger.info(f"Команда /start от пользователя {user_id}")
    
    # Загружаем данные пользователя
    users = load_users()
    user_data = users.get(user_id, {"free_used": False, "used": 0})
    remaining_free = 0 if user_data.get("free_used") else 1
    
    await message.answer(
        "🎬 **Привет! Я — КружкоРез**\n\n"
        "Я превращаю кружки из Telegram в готовые видео для Reels, Shorts и TikTok.\n\n"
        "✅ **Вертикальный формат 1080×1920**\n"
        "✅ **Черные полосы вместо белых**\n"
        "✅ **Сохранение аудио**\n"
        "✅ **Оптимизация для соцсетей**\n\n"
        f"У тебя осталось **{remaining_free} бесплатных кружков**.",
        reply_markup=get_main_keyboard()
    )

@router.callback_query(F.data == "back_to_main")
async def btn_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "Главное меню:",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "make_video")
async def btn_make_video(callback: CallbackQuery):
    await callback.message.answer(
        "📹 **Как сделать видео:**\n\n"
        "Просто **перешли мне кружок** (видеосообщение), и я обработаю его!\n\n"
        "Я преобразую его в **вертикальный формат 1080×1920** с черными полосами по бокам — идеально для Reels и Shorts!\n\n"
        "_Отправь кружок прямо сейчас..._",
        reply_markup=get_back_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "howto")
async def btn_howto(callback: CallbackQuery):
    await callback.message.answer(
        "📱 **Как пользоваться ботом:**\n\n"
        "1. 📸 **Запиши кружок** в Telegram\n"
        "   _Удерживай микрофон → проведи вверх → сними видео_\n\n"
        "2. 📤 **Перешли кружок** мне\n"
        "   _Просто перешли как обычное сообщение_\n\n"
        "3. ⏳ **Подожди обработки**\n"
        "   _Обработка в вертикальный формат займет 1-2 минуты_\n\n"
        "4. 🎬 **Получи готовое видео 1080×1920**\n"
        "   _Готово для Instagram Reels, YouTube Shorts, TikTok_\n\n"
        "⚠️ **Важно:**\n"
        "• Максимальная длительность: 60 секунд\n"
        "• Результат: видео 1080×1920 с черными полосами",
        reply_markup=get_back_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "support")
async def btn_support(callback: CallbackQuery):
    await callback.message.answer(
        f"🛠 Поддержка\n\n"
        f"Пиши сюда: {SUPPORT_USERNAME}\n\n"
        "Если бот не отвечает — возможно, идёт техническое обслуживание.\n"
        "Обычно всё работает в течение 5-10 минут.",
        reply_markup=get_back_keyboard()
    )
    await callback.answer()

# === ОБРАБОТКА КРУЖКА ===
@router.message(F.video_note)
async def handle_video_note(message: Message):
    user_id = str(message.from_user.id)
    logger.info(f"⚡ ПОЛУЧЕН КРУЖОК от пользователя {user_id}")
    
    # Защита от параллельных запросов
    if user_id in user_locks and not user_locks[user_id].done():
        logger.warning(f"Попытка параллельной обработки от {user_id}")
        await message.answer("⏳ **Идёт обработка предыдущего кружка**\n\nПодожди немного, пожалуйста.")
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

        # Проверка лимита
        if not user_data["free_used"]:
            user_data["free_used"] = True
            quota_ok = True
            is_free = True
            logger.info(f"✅ Пользователь {user_id} использует бесплатный кружок")
        else:
            logger.info(f"У пользователя {user_id} закончились бесплатные попытки")
            await message.answer(
                "🚫 **Бесплатные попытки закончились**\n\n"
                "Ты уже использовал свой бесплатный кружок.\n\n"
                "Свяжись с поддержкой для получения дополнительных возможностей.",
                reply_markup=get_main_keyboard()
            )
            return

        if quota_ok:
            # Отправляем сообщение о начале обработки
            processing_msg = await message.answer(
                "🎥 **Обрабатываю кружок...**\n\n"
                "Преобразую в вертикальный формат 1080×1920...\n"
                "Это займет 1-2 минуты.\n"
                "_Пожалуйста, подожди..._"
            )

            video_note: VideoNote = message.video_note
            logger.info(f"📊 Данные кружка: длительность={video_note.duration}сек, размер={video_note.file_size}")
            
            # Проверка длительности
            if video_note.duration > MAX_VIDEO_DURATION:
                await message.answer(
                    f"❌ **Кружок слишком длинный**\n\n"
                    f"Максимальная длительность — {MAX_VIDEO_DURATION} секунд.\n"
                    f"Твой кружок: {video_note.duration} секунд."
                )
                return

            with tempfile.TemporaryDirectory() as temp_dir:
                input_path = os.path.join(temp_dir, "input.mp4")
                output_path = os.path.join(temp_dir, "output_vertical.mp4")
                
                logger.info(f"📥 Скачиваю файл в {input_path}")
                
                try:
                    await bot.download(video_note, destination=input_path)
                    
                    if not os.path.exists(input_path):
                        raise FileNotFoundError("Файл не был скачан")
                    
                    file_size = os.path.getsize(input_path)
                    logger.info(f"✅ Файл скачан успешно. Размер: {file_size} байт")
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка скачивания файла: {e}")
                    await message.answer(
                        "❌ **Не удалось скачать кружок**\n\n"
                        "Попробуй отправить его снова.",
                        reply_markup=get_main_keyboard()
                    )
                    return

                try:
                    logger.info("⚡ Начинаю обработку в вертикальный формат 1080x1920...")
                    success = await async_process_video(input_path, output_path, video_note.duration)
                    
                    if success and os.path.exists(output_path):
                        output_size = os.path.getsize(output_path)
                        logger.info(f"✅ Обработка завершена успешно. Размер выходного файла: {output_size} байт")
                        
                        # Удаляем сообщение "обрабатываю"
                        try:
                            await processing_msg.delete()
                        except:
                            pass
                        
                        # Отправляем обработанное видео через BufferedInputFile
                        with open(output_path, 'rb') as f:
                            video_bytes = f.read()
                        
                        await message.answer_video(
                            video=BufferedInputFile(video_bytes, filename="reels_video.mp4"),
                            caption="✅ **Готово! Видео обработано в вертикальный формат 1080×1920**\n\n"
                                   "Идеально подходит для:\n"
                                   "📱 Instagram Reels\n"
                                   "📱 YouTube Shorts\n"
                                   "📱 TikTok\n"
                                   "📱 VK Клипы\n\n"
                                   "_Размер: 1080×1920 • Черные полосы • С аудио_ 🎬"
                        )
                        logger.info(f"✅ Видео отправлено пользователю {user_id}")
                    else:
                        raise RuntimeError("Не удалось обработать видео")
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки видео: {e}")
                    await message.answer(
                        "❌ **Не удалось обработать кружок**\n\n"
                        "Возможные причины:\n"
                        "• Видео слишком большое\n"
                        "• Проблемы с форматом видео\n"
                        "• Технические неполадки\n\n"
                        "Попробуй другой кружок или обратись в поддержку.",
                        reply_markup=get_main_keyboard()
                    )
                    return

            # Сохраняем данные пользователя
            users[user_id] = user_data
            save_users(users)

            if is_free:
                await message.answer(
                    "✨ **Это был твой бесплатный кружок!**\n\n"
                    "Свяжись с поддержкой, если хочешь обрабатывать больше видео.",
                    reply_markup=get_main_keyboard()
                )
                logger.info(f"🎯 Пользователь {user_id} использовал бесплатный кружок")

    except Exception as e:
        logger.error(f"🚨 Неожиданная ошибка в обработке кружка: {e}")
        await message.answer(
            "⚠️ **Произошла ошибка**\n\n"
            "Попробуй позже или обратись в поддержку.",
            reply_markup=get_main_keyboard()
        )
    finally:
        lock.set_result(True)
        user_locks.pop(user_id, None)
        logger.info(f"🏁 Обработка завершена для пользователя {user_id}")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Команда для проверки статистики"""
    ADMIN_ID = os.getenv("ADMIN_ID", "")
    
    if ADMIN_ID and str(message.from_user.id) != ADMIN_ID:
        return
    
    users = load_users()
    total_users = len(users)
    free_used = sum(1 for u in users.values() if u.get("free_used"))
    
    await message.answer(
        f"📊 **Статистика бота:**\n\n"
        f"• Всего пользователей: {total_users}\n"
        f"• Использовано бесплатных кружков: {free_used}\n"
        f"• Активных обработок: {len(user_locks)}\n"
        f"• FFmpeg: {'✅ Доступен' if ffmpeg_available else '❌ Не доступен'}\n"
        f"• Формат: Вертикальный 1080×1920\n"
        f"• Режим: {'вебхук' if os.getenv('RENDER_EXTERNAL_URL') else 'polling'}"
    )

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Команда помощи"""
    await message.answer(
        "ℹ️ **Помощь по боту:**\n\n"
        "**Основные команды:**\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать это сообщение\n"
        "/stats - Статистика (только для админа)\n\n"
        "**Как использовать:**\n"
        "1. Отправь мне кружок (видеосообщение)\n"
        "2. Я преобразую его в вертикальный формат 1080×1920\n"
        "3. Получи готовое видео для Reels/Shorts\n\n"
        "**Ограничения:**\n"
        "• Максимальная длительность: 60 секунд\n"
        "• 1 бесплатный кружок на пользователя\n"
        "• Результат: 1080×1920 с черными полосами",
        reply_markup=get_main_keyboard()
    )

@router.message(Command("health"))
async def cmd_health(message: Message):
    """Проверка здоровья бота"""
    await message.answer(
        f"🏥 **Состояние бота:**\n\n"
        f"• Статус: ✅ Работает\n"
        f"• Пользователей: {len(load_users())}\n"
        f"• Активных обработок: {len(user_locks)}\n"
        f"• FFmpeg: {'✅ Доступен' if ffmpeg_available else '❌ Не доступен'}\n"
        f"• Формат обработки: Вертикальный 1080×1920\n"
        f"• Режим: {'вебхук' if os.getenv('RENDER_EXTERNAL_URL') else 'polling'}"
    )

@router.message()
async def fallback(message: Message):
    """Обработка неизвестных сообщений"""
    await message.answer(
        "🤖 **КружкоРез**\n\n"
        "Я специализируюсь на обработке кружков (видеосообщений) в вертикальный формат.\n\n"
        "**Что я умею:**\n"
        "• Преобразовывать кружки в видео 1080×1920\n"
        "• Добавлять черные полосы для вертикального формата\n"
        "• Сохранять аудио\n"
        "• Создавать контент для Reels/Shorts\n\n"
        "**Просто перешли мне кружок или используй кнопки ниже:**",
        reply_markup=get_main_keyboard()
    )

# Подключаем роутер
dp.include_router(router)

# === ВЕБХУК ОБРАБОТЧИК ===
async def on_startup():
    """Действия при запуске бота"""
    logger.info("=" * 50)
    logger.info("🚀 Бот КружкоРез запускается...")
    logger.info(f"🎯 Режим: Вертикальная обработка 1080x1920")
    
    # Проверяем наличие папки для данных
    if not os.path.exists(USERS_FILE.parent):
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"✅ Создана папку для данных: {USERS_FILE.parent}")
    
    # Создаем файл users.json если его нет
    if not os.path.exists(USERS_FILE):
        logger.info(f"✅ Создаю новый файл {USERS_FILE}")
        save_users({})
    
    # Установка вебхука (только если есть URL)
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
            logger.error(f"❌ Ошибка установки вебхука: {e}")
    else:
        logger.info("ℹ️ RENDER_EXTERNAL_URL не установлен. Работаем в режиме polling")
    
    logger.info("=" * 50)

async def on_shutdown():
    """Действия при остановке бота"""
    logger.info("🛑 Бот останавливается...")
    # Удаляем вебхук
    try:
        await bot.delete_webhook()
        logger.info("✅ Вебхук удален")
    except:
        pass
    executor.shutdown(wait=False)

# === ЗАПУСК ЧЕРЕЗ ВЕБХУКИ ===
def start_webhook():
    """Запуск бота через вебхуки (для Render)"""
    app = web.Application()
    
    # Создаем обработчик вебхука
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot
    )
    
    # Регистрируем путь для вебхука
    webhook_handler.register(app, path="/webhook")
    
    # Регистрируем дополнительные endpoints
    async def health_check(request):
        """Health check endpoint для Render"""
        return web.Response(
            text="✅ КружкоРез бот работает\n\n"
                 f"FFmpeg: {'Доступен' if ffmpeg_available else 'Не доступен'}\n"
                 f"Пользователей: {len(load_users())}\n"
                 f"Формат: Вертикальный 1080×1920\n"
                 f"Версия: 4.0 (вертикальная обработка)",
            status=200,
            content_type="text/plain"
        )
    
    async def info(request):
        """Информационная страница"""
        users = load_users()
        return web.Response(
            text=f"🤖 КружкоРез Бот\n\n"
                 f"Статус: Активен ✅\n"
                 f"Пользователей: {len(users)}\n"
                 f"FFmpeg: {'Доступен' if ffmpeg_available else 'Не доступен'}\n"
                 f"Формат: Вертикальный 1080×1920\n"
                 f"Версия: 4.0 (финальная)\n"
                 f"Режим: Вебхук",
            status=200,
            content_type="text/plain"
        )
    
    # Добавляем маршруты
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    app.router.add_get("/info", info)
    
    # Настраиваем приложение
    setup_application(app, dp, bot=bot)
    
    # Получаем порт из переменных окружения
    port = int(os.getenv("PORT", 10000))
    
    logger.info(f"🌐 Запуск веб-сервера на порту {port}")
    logger.info(f"🎯 Режим: Вертикальная обработка 1080x1920")
    logger.info(f"⚡ Таймаут обработки: 150 секунд")
    
    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
        access_log=logger
    )

# === ЗАПУСК ЧЕРЕЗ POLLING ===
def start_polling():
    """Запуск бота через polling (для локальной разработки)"""
    from aiogram import executor as aiogram_executor
    
    logger.info("🔄 Запуск в режиме polling (локальная разработка)")
    
    aiogram_executor.start_polling(
        dp,
        skip_updates=True,
        on_startup=on_startup,
        on_shutdown=on_shutdown
    )

# === ГЛАВНАЯ ФУНКЦИЯ ===
if __name__ == "__main__":
    # Определяем режим запуска
    is_render = os.getenv("RENDER") == "true" or os.getenv("RENDER_EXTERNAL_URL")
    
    if is_render:
        # Запуск в режиме вебхука (для Render)
        logger.info("🚀 Запуск в режиме вебхука (Render)")
        logger.info(f"🎯 Вертикальная обработка видео 1080x1920")
        start_webhook()
    else:
        # Запуск в режиме polling (локально)
        logger.info("💻 Запуск в режиме polling (локально)")
        start_polling()

