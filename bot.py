import logging
import asyncio
from datetime import datetime
import pytz
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8977339136:AAGDfFjj1BuCkXzNS5aZAcghp3Cspes_lEU"
CEO_CHAT_ID = 87998099
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

GROUPS = {
    -1003843039918: {"name": "Анна Коновалова (CEO)", "role": "CEO"},
    -1003977848094: {"name": "Анна Гульвачук", "role": "старший куратор"},
    -1003698655238: {"name": "Анна Коротких", "role": "старший куратор"},
    -1002094427745: {"name": "Арина Аксенова", "role": "куратор"},
    -1003510587743: {"name": "Полина Безвинная", "role": "куратор"},
    -1003383131948: {"name": "Анна Деменковец", "role": "куратор"},
    -876729436:     {"name": "Эллина Леонова", "role": "куратор"},
    -708866901:     {"name": "Алина Ананевич", "role": "куратор"},
    -957249228:     {"name": "Милена Страх", "role": "куратор"},
    -1003740935651: {"name": "Анна Симончик", "role": "куратор"},
    -1002385621835: {"name": "Ксения Давыдова", "role": "куратор"},
    -4120894435:    {"name": "Александра Пекарина", "role": "куратор"},
    -1003946584647: {"name": "Екатерина Скубиева", "role": "куратор"},
    -1003909669370: {"name": "Анна Крылова", "role": "старший куратор"},
    -1003651433120: {"name": "Екатерина Мискевич", "role": "куратор"},
    -1002397975487: {"name": "Татьяна Робилко", "role": "куратор"},
    -1001685190818: {"name": "Виктория Именная", "role": "куратор"},
    -4006248550:    {"name": "Дарья Кцоева", "role": "куратор"},
    -1003952930387: {"name": "Елизавета Пильщикова", "role": "куратор"},
    -861357628:     {"name": "Полина Гузняк", "role": "куратор"},
    -1003925996524: {"name": "Александра Соленая", "role": "куратор"},
    -627434948:     {"name": "Вероника Власова", "role": "старший куратор"},
    -1003518314376: {"name": "Вероника Григоренко", "role": "куратор"},
    -4507855357:    {"name": "Екатерина Назарова", "role": "куратор"},
    -1002086193130: {"name": "Виктория Шваб", "role": "куратор"},
    -918694688:     {"name": "Ксения Железная", "role": "куратор"},
    -853129201:     {"name": "Лолита Кацман", "role": "куратор"},
    -1003949563241: {"name": "Валерия Бойко", "role": "куратор"},
    -1003957671027: {"name": "Алиса Путикова", "role": "куратор"},
}

submitted_today = {"morning": set(), "evening": set()}
last_reset_date = None

def reset_if_new_day():
    global last_reset_date, submitted_today
    today = datetime.now(MOSCOW_TZ).date()
    if last_reset_date != today:
        submitted_today = {"morning": set(), "evening": set()}
        last_reset_date = today

def get_period():
    now = datetime.now(MOSCOW_TZ)
    if 6 <= now.hour < 14:
        return "morning"
    elif 14 <= now.hour < 23:
        return "evening"
    return None

def is_checklist(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    return "чек-лист" in text_lower or "чек лист" in text_lower

def check_quality(text: str) -> list:
    issues = []
    text_lower = text.lower()
    if "клиент" not in text_lower and "задач" not in text_lower:
        issues.append("нет блока с клиентами или задачами")
    if "блокер" not in text_lower and "⚠️" not in text:
        issues.append("нет блока ⚠️ БЛОКЕРЫ")
    vague_words = ["всё ок", "все ок", "работаем", "в процессе", "нормально"]
    for word in vague_words:
        if word in text_lower:
            issues.append(f'размытая формулировка: "{word}"')
            break
    return issues

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat_id = update.message.chat_id
    text = update.message.text
    if chat_id not in GROUPS and chat_id != CEO_CHAT_ID:
        return
    if not is_checklist(text):
        return
    reset_if_new_day()
    period = get_period()
    if not period:
        return
    name = GROUPS.get(chat_id, {}).get("name", "Неизвестный")
    submitted_today[period].add(chat_id)
    issues = check_quality(text)
    now_str = datetime.now(MOSCOW_TZ).strftime("%H:%M")
    period_name = "утро" if period == "morning" else "вечер"
    deadline = "10:30" if period == "morning" else "19:00"
    now = datetime.now(MOSCOW_TZ)
    if period == "morning":
        late = now.hour > 10 or (now.hour == 10 and now.minute > 30)
    else:
        late = now.hour > 19 or (now.hour == 19 and now.minute > 0)
    if issues:
        quality_text = "⚠️ есть замечания:\n" + "\n".join(f"  — {i}" for i in issues)
    else:
        quality_text = "✅ качество ок"
    late_text = f" ⏰ опоздание (дедлайн {deadline})" if late else ""
    report = (
        f"📋 Чек-лист {period_name} получен\n"
        f"👤 {name}\n"
        f"🕐 {now_str}{late_text}\n"
        f"{quality_text}"
    )
    await context.bot.send_message(chat_id=CEO_CHAT_ID, text=report)

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != CEO_CHAT_ID:
        return
    reset_if_new_day()
    period = get_period()
    if not period:
        await update.message.reply_text("Сейчас не время чек-листов.")
        return
    period_name = "утро" if period == "morning" else "вечер"
    submitted = submitted_today[period]
    not_submitted = []
    for chat_id, info in GROUPS.items():
        if chat_id not in submitted:
            not_submitted.append(info["name"])
    total = len(GROUPS)
    done = len(submitted)
    text = f"📊 Сводка на {datetime.now(MOSCOW_TZ).strftime('%d.%m %H:%M')} ({period_name})\n\n"
    text += f"✅ Сдали: {done}/{total}\n"
    if not_submitted:
        text += f"\n❌ Не сдали ({len(not_submitted)}):\n"
        for name in not_submitted:
            text += f"— {name}\n"
    else:
        text += "\n🎉 Все сдали!"
    await update.message.reply_text(text)

async def send_summary(context: ContextTypes.DEFAULT_TYPE):
    reset_if_new_day()
    period = get_period()
    if not period:
        return
    period_name = "утро" if period == "morning" else "вечер"
    submitted = submitted_today[period]
    not_submitted = []
    for chat_id, info in GROUPS.items():
        if chat_id not in submitted:
            not_submitted.append(info["name"])
    total = len(GROUPS)
    done = len(submitted)
    text = f"📊 Автосводка ({period_name})\n\n"
    text += f"✅ Сдали: {done}/{total}\n"
    if not_submitted:
        text += f"\n❌ Не сдали ({len(not_submitted)}):\n"
        for name in not_submitted:
            text += f"— {name}\n"
    else:
        text += "\n🎉 Все сдали!"
    await context.bot.send_message(chat_id=CEO_CHAT_ID, text=text)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.add_handler(CommandHandler("summary", cmd_summary))
    tz = pytz.timezone("Europe/Moscow")
    app.job_queue.run_daily(
        send_summary,
        time=__import__('datetime').time(10, 35, tzinfo=tz)
    )
    app.job_queue.run_daily(
        send_summary,
        time=__import__('datetime').time(19, 5, tzinfo=tz)
    )
    logger.info("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
