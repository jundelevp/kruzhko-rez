import os
import json
import tempfile
import logging
import asyncio
import urllib.parse
import hashlib
from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, VideoNote, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command
from moviepy.editor import VideoFileClip, CompositeVideoClip, ColorClip

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ROBOKASSA_LOGIN = os.getenv("ROBOKASSA_LOGIN", "")
ROBOKASSA_PASSWORD1 = os.getenv("ROBOKASSA_PASSWORD1", "")
CURRENCY = "RUB"
MAX_VIDEO_DURATION = 60  # секунд
FREE_LIMIT = 1
PREMIUM_QUOTA = 15
PRICE = 199

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен")

# === ЛОГИРОВАНИЕ ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ===
USERS_FILE = "users.json"
user_locks = {}  # для защиты от параллельной обработки одного пользователя
executor = ThreadPoolExecutor(max_workers=2)  # ограничение одновременных обработок

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# === БЕЗОПАСНАЯ РАБОТА С JSON ===
@contextmanager
def safe_json_write(filepath):
    temp_path = filepath + ".tmp"
    try:
        yield temp_path
        os.replace(temp_path, filepath)  # Атомарная замена
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки {USERS_FILE}: {e}")
        return {}

def save_users(users):
    try:
        with safe_json_write(USERS_FILE) as temp_path:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения {USERS_FILE}: {e}")

# === РОБОКАССА ===
def generate_robokassa_url(user_id: int) -> str:
    description = f"КружкоРез: {PREMIUM_QUOTA} кружков за {PRICE} руб. (ID: {user_id})"
    desc_encoded = urllib.parse.quote(description, safe='')
    inv_id = str(user_id)
    out_sum = f"{PRICE}.00"
    signature = f"{ROBOKASSA_LOGIN}:{out_sum}:{inv_id}:{ROBOKASSA_PASSWORD1}"
    signature_md5 = hashlib.md5(signature.encode("utf-8")).hexdigest()
    return (
        f"https://auth.robokassa.ru/Merchant/Index.aspx"
        f"?MrchLogin={ROBOKASSA_LOGIN}"
        f"&OutSum={out_sum}"
        f"&InvId={inv_id}"
        f"&Desc={desc_encoded}"
        f"&SignatureValue={signature_md5}"
        f"&Encoding=utf-8"
    )

# === КЛАВИАТУРА ===
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎥 Сделать видео", callback_data="make_video")],
        [InlineKeyboardButton(text=f"💳 Оплатить ({PRICE}₽ → {PREMIUM_QUOTA} кружков)", callback_data="pay")],
        [InlineKeyboardButton(text="ℹ️ Как пользоваться", callback_data="howto")],
        [InlineKeyboardButton(text="🛠 Поддержка", callback_data="support")]
    ])

# === ОБРАБОТКА СООБЩЕНИЙ ===
@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я — **КружкоРез**.\n\n"
        "Я превращаю кружки из Telegram в готовые видео для Reels, Shorts и TikTok.\n"
        "✅ Поддержка аудио\n"
        "✅ Вертикальный формат 1080×1920\n"
        "✅ Без белого фона\n\n"
        f"У тебя есть **{FREE_LIMIT} бесплатный кружок**. Дальше — {PRICE}₽ за {PREMIUM_QUOTA} кружков.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@router.callback_query(F.data == "make_video")
async def btn_make_video(callback: CallbackQuery):
    await callback.message.answer("Просто **перешли сюда кружок** (голосовое с видео), и я обработаю его.")
    await callback.answer()

@router.callback_query(F.data == "howto")
async def btn_howto(callback: CallbackQuery):
    await callback.message.answer(
        "1. Запиши кружок в Telegram (удерживай микрофон → проведи вверх → сними видео)\n"
        "2. Перешли его мне\n"
        "3. Получи готовое видео для Instagram, VK, YouTube\n\n"
        "❗ Видео должно быть не длиннее 1 минуты."
    )
    await callback.answer()

@router.callback_query(F.data == "support")
async def btn_support(callback: CallbackQuery):
    await callback.message.answer(
        "Пиши сюда: @your_support_username\n\n"
        "Если бот не отвечает — возможно, идёт техническое обслуживание."
    )
    await callback.answer()

@router.callback_query(F.data == "pay")
async def btn_pay(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    users = load_users()
    if users.get(userid, {}).get("premium"):
        await callback.message.answer("✅ У тебя уже есть доступ! Отправляй кружки.")
    else:
        payment_url = generate_robokassa_url(callback.from_user.id)
        await callback.message.answer(
            f"Нажми на кнопку ниже, чтобы оплатить **{PRICE}₽ за {PREMIUM_QUOTA} кружков**.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"Оплатить {PRICE}₽", url=payment_url)]
            ])
        )
    await callback.answer()

# === ОБРАБОТКА КРУЖКА ===
async def async_process_video(input_path: str, output_path: str, duration: float):
    """Обработка видео в отдельном потоке с таймаутом"""
    def _process():
        clip = VideoFileClip(input_path).resize(640)
        if clip.duration > MAX_VIDEO_DURATION:
            clip = clip.subclip(0, MAX_VIDEO_DURATION)
        bg = ColorClip(size=(1080, 1920), color=(0, 0, 0), duration=clip.duration)
        final = CompositeVideoClip([bg, clip.set_position("center")])
        final.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=os.path.join(os.path.dirname(output_path), "temp-audio.m4a"),
            remove_temp=True,
            fps=24,
            logger=None,
            threads=1  # экономия CPU
        )
        clip.close()
        bg.close()
        final.close()

    loop = asyncio.get_event_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(executor, _process),
            timeout=60.0
        )
    except asyncio.TimeoutError:
        raise RuntimeError("Видео слишком большое — обработка отменена.")

@router.message(F.video_note)
async def handle_video_note(message: Message):
    user_id = str(message.from_user.id)
    # Защита от параллельных запросов
    if user_id in user_locks and not user_locks[user_id].done():
        await message.answer("⏳ Идёт обработка предыдущего кружка. Подожди немного.")
        return

    lock = asyncio.Future()
    user_locks[user_id] = lock

    try:
        users = load_users()
        user_data = users.get(user_id, {"free_used": False, "used": 0, "premium": False})

        # Проверка лимита
        if not user_data["premium"]:
            if not user_data["free_used"]:
                user_data["free_used"] = True
                quota_ok = True
                is_free = True
            else:
                await message.answer(
                    f"🚫 Бесплатные попытки закончились.\n\n"
                    f"Нажми «Оплатить» в меню, чтобы разблокировать {PREMIUM_QUOTA} кружков за {PRICE}₽.",
                    reply_markup=get_main_keyboard()
                )
                return
        else:
            if user_data["used"] >= PREMIUM_QUOTA:
                await message.answer(f"⚠️ Ты использовал все {PREMIUM_QUOTA} кружков.\n\nОплати ещё раз, чтобы продолжить.")
                return
            user_data["used"] += 1
            quota_ok = True
            is_free = False

        if quota_ok:
            await message.answer("🎥 Обрабатываю кружок... (до 30 сек)")

            video_note: VideoNote = message.video_note
            if video_note.duration > MAX_VIDEO_DURATION:
                await message.answer("❌ Кружок слишком длинный. Максимум — 60 секунд.")
                return

            with tempfile.TemporaryDirectory() as temp_dir:
                input_path = os.path.join(temp_dir, "input.mp4")
                output_path = os.path.join(temp_dir, "output.mp4")

                await bot.download(video_note, destination=input_path)

                try:
                    await async_process_video(input_path, output_path, video_note.duration)
                    if os.path.exists(output_path):
                        await message.answer_video(video=output_path, caption="✅ Готово! Сохраняй и выкладывай в Reels/Shorts.")
                    else:
                        raise RuntimeError("Файл не создан")
                except Exception as e:
                    logger.error(f"Ошибка обработки: {e}")
                    await message.answer("❌ Не удалось обработать кружок. Попробуй другой.")
                    return

            # Сохраняем данные
            users[user_id] = user_data
            save_users(users)

            if is_free:
                await message.answer(
                    f"✨ Это был твой **бесплатный кружок**!\n\n"
                    f"Дальше — {PRICE}₽ за {PREMIUM_QUOTA} кружков.",
                    reply_markup=get_main_keyboard()
                )

    finally:
        lock.set_result(True)
        user_locks.pop(user_id, None)

@router.message()
async def fallback(message: Message):
    await message.answer("Пожалуйста, используй кнопки или отправь кружок.", reply_markup=get_main_keyboard())

dp.include_router(router)

if __name__ == "__main__":
    logger.info("🚀 Запуск КружкоРез...")
    asyncio.run(dp.start_polling(bot))