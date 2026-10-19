"""
neira_proof · Лаборатория Нейры — Telegram-бот воронки «ЛАБ».

Как работает:
  Instagram: человек пишет «ЛАБ» в комментарии → в директ приходит ссылка вида
  https://t.me/<бот>?start=<код> → бот проверяет подписку на канал → отдаёт файл.

Коды материалов (deep link) задаются в materials.json.
"""
import asyncio
import csv
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")
load_dotenv(BASE / "env.txt")  # запасной вариант: настройки в обычном текстовом файле (удобно с планшета)

BOT_TOKEN = os.environ["BOT_TOKEN"]                       # токен от @BotFather
CHANNEL = os.environ["CHANNEL"]                           # @username канала или -100… id
CHANNEL_URL = os.environ.get("CHANNEL_URL") or f"https://t.me/{CHANNEL.lstrip('@')}"
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DEFAULT_CODE = os.environ.get("DEFAULT_CODE", "lab50")

MATERIALS = json.loads((BASE / "materials.json").read_text(encoding="utf-8"))
DATA_DIR = Path(os.environ.get("DATA_DIR") or (BASE / "data"))  # на Amvera: /data (постоянное хранилище)
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "events.csv"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
dp = Dispatcher()


# ---------- журнал для аналитика результатов ----------
def log_event(user, event: str, code: str = "") -> None:
    new = not LOG_FILE.exists()
    with LOG_FILE.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["utc_time", "user_id", "username", "event", "code"])
        w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    user.id, user.username or "", event, code])


# ---------- тексты (голос Нейры, бренд-бук v1.1) ----------
TXT_HELLO = (
    "🧪 <b>Лаборатория Нейры на связи</b>\n\n"
    "Я — Нейра. Меня не существует. А вот файл, за которым ты здесь, — очень даже.\n\n"
)
TXT_NEED_SUB = (
    "Осталось одно действие: подпишись на канал лаборатории. "
    "Там я каждый день тестирую нейросети и отдаю промпты без воды.\n\n"
    "Уже в канале? Жми «Проверить» — и файл твой."
)
TXT_NOT_YET = "Хм, пока не вижу подписки 👀 Подпишись на канал и нажми «Проверить» ещё раз."
TXT_GIVE = "Держи 👇 Сохрани, чтобы не потерять.\n\n<b>{title}</b>\n{blurb}"
TXT_AFTER = (
    "Зашло? Напиши в комментариях канала, какой промпт сработал лучше всего — "
    "следующий набор соберу под ваши задачи.\n\n"
    "Проверено в лаборатории 🧪"
)
TXT_MENU = "Вот что сейчас есть в лаборатории — выбирай 👇"


def kb_subscribe(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 Подписаться на канал", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Проверить", callback_data=f"chk:{code}")],
    ])


def kb_menu() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=m["button"], callback_data=f"get:{c}")]
            for c, m in MATERIALS.items() if m.get("in_menu", True)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def is_subscribed(bot: Bot, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(CHANNEL, user_id)
    except Exception as e:  # бот не админ канала или неверный CHANNEL
        logging.error("get_chat_member failed: %s", e)
        return False
    return m.status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                        ChatMemberStatus.CREATOR}


async def deliver(bot: Bot, chat_id: int, user, code: str) -> None:
    m = MATERIALS[code]
    await bot.send_message(chat_id, TXT_GIVE.format(title=m["title"], blurb=m["blurb"]))
    path = BASE / "materials" / m["file"]
    if not path.exists():
        path = BASE / m["file"]  # файлы можно положить и рядом с bot.py
    if path.exists():
        await bot.send_document(chat_id, FSInputFile(path, filename=m["file"]))
    else:
        logging.error("Нет файла %s", path)
        await bot.send_message(chat_id, "Файл обновляется, загляни через пару минут 🛠")
    await bot.send_message(chat_id, TXT_AFTER)
    log_event(user, "delivered", code)


async def gate(bot: Bot, chat_id: int, user, code: str) -> None:
    """Проверка подписки → выдача или просьба подписаться."""
    if await is_subscribed(bot, user.id):
        await deliver(bot, chat_id, user, code)
    else:
        log_event(user, "need_sub", code)
        await bot.send_message(chat_id, TXT_NEED_SUB, reply_markup=kb_subscribe(code))


# ---------- обработчики ----------
@dp.message(CommandStart())
async def on_start(msg: Message, command: CommandObject, bot: Bot):
    code = (command.args or "").strip().lower() or DEFAULT_CODE
    if code not in MATERIALS:
        code = DEFAULT_CODE
    log_event(msg.from_user, "start", code)
    await msg.answer(TXT_HELLO)
    await gate(bot, msg.chat.id, msg.from_user, code)


@dp.message(Command("menu"))
async def on_menu(msg: Message):
    await msg.answer(TXT_MENU, reply_markup=kb_menu())


@dp.callback_query(F.data.startswith("chk:") | F.data.startswith("get:"))
async def on_check(cb: CallbackQuery, bot: Bot):
    code = cb.data.split(":", 1)[1]
    if code not in MATERIALS:
        await cb.answer("Этот материал уже убрали из лаборатории", show_alert=True)
        return
    if await is_subscribed(bot, cb.from_user.id):
        await cb.answer("Подписка есть ✅")
        await deliver(bot, cb.message.chat.id, cb.from_user, code)
    else:
        log_event(cb.from_user, "check_failed", code)
        await cb.answer(TXT_NOT_YET, show_alert=True)


@dp.message(F.text.lower().contains("лаб") | F.text.lower().contains("lab"))
async def on_keyword(msg: Message, bot: Bot):
    log_event(msg.from_user, "keyword", DEFAULT_CODE)
    await gate(bot, msg.chat.id, msg.from_user, DEFAULT_CODE)


@dp.message(Command("stats"))
async def on_stats(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    if not LOG_FILE.exists():
        await msg.answer("Пока пусто.")
        return
    rows = list(csv.DictReader(LOG_FILE.open(encoding="utf-8")))
    users = {r["user_id"] for r in rows}
    by = {}
    for r in rows:
        by.setdefault(r["code"], {}).setdefault(r["event"], set()).add(r["user_id"])
    lines = [f"<b>Всего людей:</b> {len(users)}"]
    for code, ev in by.items():
        st = len(ev.get("start", set()) | ev.get("keyword", set()))
        dl = len(ev.get("delivered", set()))
        ns = len(ev.get("need_sub", set()))
        conv = f"{dl / st:.0%}" if st else "—"
        lines.append(f"\n<b>{code}</b>: пришли {st} · просили подписку {ns} · получили {dl} · конверсия {conv}")
    await msg.answer("\n".join(lines))


@dp.message(Command("export"))
async def on_export(msg: Message):
    if msg.from_user.id in ADMIN_IDS and LOG_FILE.exists():
        await msg.answer_document(FSInputFile(LOG_FILE, filename="neira_bot_events.csv"))


@dp.message()
async def on_other(msg: Message):
    await msg.answer("Я пока понимаю только кодовые слова 🙈 Напиши «ЛАБ» или открой /menu")


async def main():
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    logging.info("Бот @%s запущен. Ссылки:", me.username)
    for c in MATERIALS:
        logging.info("  %s → https://t.me/%s?start=%s", MATERIALS[c]["title"], me.username, c)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
