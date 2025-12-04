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
    Message, VideoNote, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# === НАСТРОЙКИ ДЛЯ RENDER ===
# ВРЕМЕННО ДЛЯ ТЕСТА - ВСТАВЬТЕ СВОЙ ТОКЕН ЗДЕСЬ
BOT_TOKEN = "8535285877:AAFkJEwV18KFCnEJPAyTR2AsSsgvQbTA6fg"

# Пока отключаем проверку
# WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "DEFAULT_SECRET_TOKEN_CHANGE_ME")

# ВРЕМЕННО ЗАКОММЕНТИРОВАТЬ ЭТУ ПРОВЕРКУ!
# if not BOT_TOKEN:
#     raise ValueError("❌ BOT_TOKEN не установлен. Установите в настройках Render")

# Выведем токен для отладки
print(f"🚀 Бот запускается. Токен: {BOT_TOKEN[:10]}...")

CURRENCY = "RUB"
MAX_VIDEO_DURATION = 60
FREE_LIMIT = 1
PREMIUM_QUOTA = 15
PRICE = 199
SUPPORT_USERNAME = "@Oblastyle"

# === ЛОГИРОВАНИЕ ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

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
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()
router = Router()

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

# === ОБРАБОТКА ВИДЕО ===
async def async_process_video(input_path: str, output_path: str, duration: float):
    """Обработка видео для Render"""
    def _process():
        try:
            if not os.path.exists(input_path):
                raise FileNotFoundError(f"Входной файл не найден: {input_path}")
            
            file_size = os.path.getsize(input_path)
            if file_size == 0:
                raise ValueError("Входной файл пустой")
            
            logger.info(f"Начало обработки видео. Размер: {file_size} байт")
            
            # Проверяем FFmpeg
            try:
                subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
                ffmpeg_available = True
            except:
                ffmpeg_available = False
                logger.warning("FFmpeg не найден, используем упрощенную обработку")
            
            if ffmpeg_available:
                # Обработка FFmpeg
                cmd = [
                    'ffmpeg',
                    '-i', input_path,
                    '-vf', 'scale=640:-1:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black',
                    '-c:v', 'libx264',
                    '-preset', 'fast',
                    '-c:a', 'aac',
                    '-b:a', '128k',
                    '-y',
                    output_path
                ]
                
                logger.info("Запуск FFmpeg")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                
                if result.returncode != 0:
                    logger.error(f"FFmpeg ошибка: {result.stderr}")
                    
                    # Резервный вариант - пробуем упрощенную команду
                    simple_cmd = [
                        'ffmpeg',
                        '-i', input_path,
                        '-vf', 'scale=1080:1920,setsar=1',
                        '-c:v', 'libx264',
                        '-c:a', 'copy',
                        '-y',
                        output_path
                    ]
                    
                    result = subprocess.run(simple_cmd, capture_output=True, text=True, timeout=120)
                    if result.returncode != 0:
                        raise RuntimeError(f"FFmpeg failed: {result.stderr}")
            else:
                # Резервный вариант - используем moviepy
                try:
                    from moviepy.editor import VideoFileClip
                    
                    clip = VideoFileClip(input_path)
                    
                    # Если видео слишком длинное, обрезаем
                    if clip.duration > MAX_VIDEO_DURATION:
                        clip = clip.subclip(0, MAX_VIDEO_DURATION)
                    
                    # Ресайзим
                    clip_resized = clip.resize(height=1920)
                    
                    # Сохраняем
                    clip_resized.write_videofile(
                        output_path,
                        codec="libx264",
                        audio_codec="aac",
                        verbose=False,
                        logger=None
                    )
                    
                    clip.close()
                    clip_resized.close()
                    
                    logger.info("Видео обработано через MoviePy")
                except ImportError:
                    # Если moviepy нет - просто копируем
                    import shutil
                    shutil.copy2(input_path, output_path)
                    logger.info("Использовано простое копирование файла")
            
            if not os.path.exists(output_path):
                raise FileNotFoundError(f"Выходной файл не создан: {output_path}")
            
            output_size = os.path.getsize(output_path)
            logger.info(f"Обработка завершена. Размер выходного файла: {output_size} байт")
            
        except subprocess.TimeoutExpired:
            logger.error("Таймаут обработки FFmpeg")
            raise RuntimeError("Обработка заняла слишком много времени")
        except Exception as e:
            logger.error(f"Ошибка обработки видео: {e}")
            raise

    loop = asyncio.get_event_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(executor, _process),
            timeout=180.0  # 3 минуты
        )
    except asyncio.TimeoutError:
        logger.error("Таймаут асинхронной обработки")
        raise RuntimeError("Видео слишком большое — обработка отменена.")

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
        "✅ Поддержка аудио\n"
        "✅ Вертикальный формат 1080×1920\n"
        "✅ Без белого фона\n\n"
        f"У тебя осталось **{remaining_free} бесплатных кружков**.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
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
        "_Отправь кружок прямо сейчас..._",
        reply_markup=get_back_keyboard(),
        parse_mode="Markdown"
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
        "   _Обычно занимает 30-60 секунд_\n\n"
        "4. 🎬 **Получи готовое видео**\n"
        "   _Готово для Instagram, VK, YouTube_\n\n"
        "⚠️ **Важно:** Видео должно быть не длиннее 60 секунд.",
        reply_markup=get_back_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "support")
async def btn_support(callback: CallbackQuery):
    await callback.message.answer(
        f"🛠 **Поддержка**\n\n"
        f"Пиши сюда: {SUPPORT_USERNAME}\n\n"
        "Если бот не отвечает — возможно, идёт техническое обслуживание.\n"
        "Обычно всё работает в течение 5-10 минут.",
        reply_markup=get_back_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()

# === ОБРАБОТКА КРУЖКА ===
@router.message(F.video_note)
async def handle_video_note(message: Message):
    user_id = str(message.from_user.id)
    logger.info(f"Получен кружок от пользователя {user_id}")
    
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
            logger.info(f"Пользователь {user_id} использует бесплатный кружок")
        else:
            logger.info(f"У пользователя {user_id} закончились бесплатные попытки")
            await message.answer(
                "🚫 **Бесплатные попытки закончились**\n\n"
                "Ты уже использовал свой бесплатный кружок.\n\n"
                "Свяжись с поддержкой для получения дополнительных возможностей.",
                reply_markup=get_main_keyboard(),
                parse_mode="Markdown"
            )
            return

        if quota_ok:
            # Отправляем сообщение о начале обработки
            processing_msg = await message.answer(
                "🎥 **Обрабатываю кружок...**\n\n"
                "Это займет примерно 30-60 секунд.\n"
                "_Пожалуйста, подожди..._",
                parse_mode="Markdown"
            )

            video_note: VideoNote = message.video_note
            logger.info(f"Данные кружка: длительность={video_note.duration}сек, размер={video_note.file_size}")
            
            # Проверка длительности
            if video_note.duration > MAX_VIDEO_DURATION:
                await message.answer(
                    f"❌ **Кружок слишком длинный**\n\n"
                    f"Максимальная длительность — {MAX_VIDEO_DURATION} секунд.\n"
                    f"Твой кружок: {video_note.duration} секунд.",
                    parse_mode="Markdown"
                )
                return

            with tempfile.TemporaryDirectory() as temp_dir:
                input_path = os.path.join(temp_dir, "input.mp4")
                output_path = os.path.join(temp_dir, "output.mp4")
                
                logger.info(f"Скачиваю файл в {input_path}")
                
                try:
                    await bot.download(video_note, destination=input_path)
                    
                    if not os.path.exists(input_path):
                        raise FileNotFoundError("Файл не был скачан")
                    
                    file_size = os.path.getsize(input_path)
                    logger.info(f"Файл скачан успешно. Размер: {file_size} байт")
                    
                except Exception as e:
                    logger.error(f"Ошибка скачивания файла: {e}")
                    await message.answer(
                        "❌ **Не удалось скачать кружок**\n\n"
                        "Попробуй отправить его снова.",
                        parse_mode="Markdown"
                    )
                    return

                try:
                    logger.info("Начинаю обработку видео...")
                    await async_process_video(input_path, output_path, video_note.duration)
                    
                    if os.path.exists(output_path):
                        output_size = os.path.getsize(output_path)
                        logger.info(f"Обработка завершена успешно. Размер выходного файла: {output_size} байт")
                        
                        # Удаляем сообщение "обрабатываю"
                        try:
                            await processing_msg.delete()
                        except:
                            pass
                        
                        # Отправляем результат
                        await message.answer_video(
                            video=output_path,
                            caption="✅ **Готово!**\n\n"
                                   "Сохраняй видео и выкладывай в:\n"
                                   "• Instagram Reels\n"
                                   "• YouTube Shorts\n"
                                   "• TikTok\n"
                                   "• VK Клипы\n\n"
                                   "_Приятного использования!_ 🎬",
                            parse_mode="Markdown"
                        )
                    else:
                        raise RuntimeError("Выходной файл не создан")
                        
                except Exception as e:
                    logger.error(f"Ошибка обработки видео: {e}")
                    await message.answer(
                        "❌ **Не удалось обработать кружок**\n\n"
                        "Возможные причины:\n"
                        "• Видео слишком большое\n"
                        "• Проблемы с форматом видео\n"
                        "• Технические неполадки\n\n"
                        "Попробуй другой кружок или обратись в поддержку.",
                        reply_markup=get_main_keyboard(),
                        parse_mode="Markdown"
                    )
                    return

            # Сохраняем данные пользователя
            users[user_id] = user_data
            save_users(users)

            if is_free:
                await message.answer(
                    "✨ **Это был твой бесплатный кружок!**\n\n"
                    "Свяжись с поддержкой, если хочешь обрабатывать больше видео.",
                    reply_markup=get_main_keyboard(),
                    parse_mode="Markdown"
                )
                logger.info(f"Пользователь {user_id} использовал бесплатный кружок")

    except Exception as e:
        logger.error(f"Неожиданная ошибка в обработке кружка: {e}")
        await message.answer(
            "⚠️ **Произошла ошибка**\n\n"
            "Попробуй позже или обратись в поддержку.",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
    finally:
        lock.set_result(True)
        user_locks.pop(user_id, None)
        logger.info(f"Обработка завершена для пользователя {user_id}")

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
        f"• Режим: {'вебхук' if os.getenv('RENDER_EXTERNAL_URL') else 'polling'}",
        parse_mode="Markdown"
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
        "2. Я обработаю его в вертикальный формат\n"
        "3. Получи готовое видео для соцсетей\n\n"
        "**Ограничения:**\n"
        "• Максимальная длительность: 60 секунд\n"
        "• 1 бесплатный кружок на пользователя",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@router.message(Command("health"))
async def cmd_health(message: Message):
    """Проверка здоровья бота"""
    # Проверяем доступность FFmpeg
    ffmpeg_check = subprocess.run(['which', 'ffmpeg'], capture_output=True, text=True)
    ffmpeg_status = "✅ Доступен" if ffmpeg_check.returncode == 0 else "❌ Не доступен"
    
    await message.answer(
        f"🏥 **Состояние бота:**\n\n"
        f"• Статус: ✅ Работает\n"
        f"• Пользователей: {len(load_users())}\n"
        f"• Активных обработок: {len(user_locks)}\n"
        f"• FFmpeg: {ffmpeg_status}\n"
        f"• Режим: {'вебхук' if os.getenv('RENDER_EXTERNAL_URL') else 'polling'}",
        parse_mode="Markdown"
    )

@router.message()
async def fallback(message: Message):
    """Обработка неизвестных сообщений"""
    await message.answer(
        "🤖 **КружкоРез**\n\n"
        "Я специализируюсь на обработке кружков (видеосообщений).\n\n"
        "**Что я умею:**\n"
        "• Обрабатывать кружки в вертикальное видео\n"
        "• Сохранять аудио\n"
        "• Создавать контент для Reels/Shorts\n\n"
        "**Просто перешли мне кружок или используй кнопки ниже:**",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

# Подключаем роутер
dp.include_router(router)

# === ВЕБХУК ОБРАБОТЧИК ===
async def on_startup():
    """Действия при запуске бота"""
    logger.info("=" * 50)
    logger.info("🚀 Бот КружкоРез запускается...")
    
    # Проверка FFmpeg
    ffmpeg_check = subprocess.run(['which', 'ffmpeg'], capture_output=True, text=True)
    if ffmpeg_check.returncode == 0:
        version_check = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        logger.info(f"✅ FFmpeg доступен")
    else:
        logger.warning("⚠️ FFmpeg не найден. Пытаемся установить...")
        try:
            # Пробуем установить FFmpeg если нет
            subprocess.run(['apt-get', 'update'], capture_output=True)
            subprocess.run(['apt-get', 'install', '-y', 'ffmpeg'], capture_output=True)
            logger.info("✅ FFmpeg установлен")
        except:
            logger.warning("⚠️ Не удалось установить FFmpeg. Будет использован упрощенный режим")
    
    # Проверяем наличие папки для данных
    if not os.path.exists(USERS_FILE.parent):
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"✅ Создана папка для данных: {USERS_FILE.parent}")
    
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
        bot=bot,
        secret_token=WEBHOOK_SECRET_TOKEN
    )
    
    # Регистрируем путь для вебхука
    webhook_handler.register(app, path="/webhook")
    
    # Регистрируем дополнительные endpoints
    async def health_check(request):
        """Health check endpoint для Render"""
        return web.Response(
            text="✅ КружкоРез бот работает\n\n"
                 "Бот активен и готов обрабатывать видеосообщения.",
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
                 f"Версия: 1.0\n"
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
    logger.info(f"🔐 Секретный токен вебхука: {WEBHOOK_SECRET_TOKEN[:10]}...")
    logger.info(f"📊 Health check: http://0.0.0.0:{port}/health")
    
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
        start_webhook()
    else:
        # Запуск в режиме polling (локально)
        logger.info("💻 Запуск в режиме polling (локально)")

        start_polling()


