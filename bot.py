import logging
import re
from datetime import time
import pytz
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    ContextTypes, filters
)

# ─── НАСТРОЙКИ ───────────────────────────────────────────────
import os
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_СЮДА")
NOTIFY_CHAT_ID = int(os.environ.get("NOTIFY_CHAT_ID", "0"))  # личка CEO
SPREADSHEET_ID = "1lDKXBR7URApkfDqCZ6RMtPn283ZlV50MjxdwauvyW9Q"
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── СТРУКТУРА: СТАРШИЙ → ЕГО КУРАТОРЫ ──────────────────────
SENIOR_TO_CURATORS = {
    "0":    [11, 12, 13, 14, 15, 16, 17, 18, 19, 20],  # Коротких
    "00":   [21, 22, 23, 24, 27, 28, 30],               # Крылова
    "0000": [41, 42, 43, 44, 45, 46, 47],               # Власова
}

# ─── GOOGLE SHEETS ───────────────────────────────────────────
def get_sheets_client():
    import os, json
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

def get_active_clients_for_unit(unit_number):
    """Возвращает список активных клиентов для юнита (нижний регистр, фамилия имя)"""
    try:
        gc = get_sheets_client()
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet("Отчет")
        all_values = ws.get_all_values()
        if not all_values:
            return []

        headers = all_values[0]
        def col(name):
            for i, h in enumerate(headers):
                if name.lower() in h.lower():
                    return i
            return -1

        idx_unit = col("юнит")
        idx_status = col("активн")
        idx_name = col("фамилия имя")

        if idx_unit == -1 or idx_name == -1:
            logger.error(f"Не найдены колонки. Заголовки: {headers}")
            return []

        unit_str = str(unit_number)
        clients = []
        for row in all_values[1:]:
            if len(row) <= max(idx_unit, idx_name):
                continue
            row_unit = str(row[idx_unit]).replace("Юнит ", "").strip()
            status = str(row[idx_status]).strip() if idx_status != -1 else "активный"
            name = str(row[idx_name]).strip()
            if row_unit == unit_str and status.lower() == "активный" and name:
                clients.append(name.lower())
        return clients
    except Exception as e:
        logger.error(f"Ошибка чтения Sheets: {e}")
        return []

def get_curator_info(chat_id):
    """Возвращает (unit, role, full_name) по chat_id"""
    try:
        gc = get_sheets_client()
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet("Кураторы")
        rows = ws.get_all_records()

        for row in rows:
            if str(row.get("chat_id", "")).strip() == str(chat_id):
                unit = str(row.get("юнит", "")).strip()
                role = str(row.get("роль", "")).strip()
                name = f"{row.get('имя', '')} {row.get('фамилия', '')}".strip()
                return unit, role, name
    except Exception as e:
        logger.error(f"Ошибка чтения Кураторов: {e}")
    return None, None, None

def get_curators_for_senior(senior_unit):
    """Возвращает список (unit, full_name) кураторов старшего"""
    try:
        gc = get_sheets_client()
        sh = gc.open_by_key(SPREADSHEET_ID)
        ws = sh.worksheet("Кураторы")
        rows = ws.get_all_records()

        junior_units = SENIOR_TO_CURATORS.get(str(senior_unit), [])
        curators = []
        for row in rows:
            unit = str(row.get("юнит", "")).strip()
            role = str(row.get("роль", "")).strip()
            if role == "куратор":
                try:
                    if int(unit) in junior_units:
                        name = f"{row.get('имя', '')} {row.get('фамилия', '')}".strip()
                        curators.append((unit, name))
                except:
                    pass
        return curators
    except Exception as e:
        logger.error(f"Ошибка получения кураторов: {e}")
        return []

# ─── ПРОВЕРКА ЧЕК-ЛИСТА ──────────────────────────────────────
def check_checklist(text, active_clients, curator_names=None):
    """
    Возвращает (missing_clients, missing_curators, warnings)
    active_clients — список строк вида "фамилия имя" в нижнем регистре
    curator_names — список имён кураторов для старших (или None)
    """
    text_lower = text.lower()

    # Клиенты не упомянуты
    missing_clients = []
    for client in active_clients:
        # Ищем хотя бы фамилию клиента
        last_name = client.split()[0] if client.split() else client
        if last_name not in text_lower:
            missing_clients.append(client)

    # Кураторы не упомянуты (для старших)
    missing_curators = []
    if curator_names:
        for (unit, name) in curator_names:
            last_name = name.split()[-1].lower() if name.split() else name.lower()
            if last_name not in text_lower:
                missing_curators.append(name)

    # Общие предупреждения
    warnings = []
    if "блокер" not in text_lower and "проблем" not in text_lower:
        warnings.append("нет блока с блокерами/проблемами")

    return missing_clients, missing_curators, warnings

def is_checklist(text):
    text_lower = text.lower()
    return "чек-лист" in text_lower or "чек лист" in text_lower

# ─── ХРАНИЛИЩЕ СТАТУСОВ ──────────────────────────────────────
checklist_status = {}  # chat_id → {"name": ..., "unit": ..., "morning": bool, "evening": bool}

def update_status(chat_id, name, unit, period):
    if chat_id not in checklist_status:
        checklist_status[chat_id] = {"name": name, "unit": unit, "morning": False, "evening": False}
    checklist_status[chat_id][period] = True

# ─── ОБРАБОТЧИК СООБЩЕНИЙ ────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text
    chat_id = msg.chat_id

    if not is_checklist(text):
        return

    unit, role, name = get_curator_info(chat_id)
    if not unit and role != "CEO":
        return

    # Определяем период
    import datetime
    now = datetime.datetime.now(MOSCOW_TZ)
    period = "morning" if now.hour < 14 else "evening"
    period_label = "утро" if period == "morning" else "вечер"

    # Активные клиенты
    active_clients = get_active_clients_for_unit(unit) if unit else []

    # Кураторы (для старших)
    curator_names = None
    if role == "старший куратор":
        curator_names = get_curators_for_senior(unit)

    missing_clients, missing_curators, warnings = check_checklist(
        text, active_clients, curator_names
    )

    update_status(chat_id, name, unit, period)

    # Формируем отчёт
    lines = [f"📋 Чек-лист получен: *{name}* ({period_label})"]

    if not missing_clients and not missing_curators and not warnings:
        lines.append("✅ Всё в порядке")
    else:
        if missing_clients:
            lines.append(f"\n⚠️ *Не упомянуты клиенты ({len(missing_clients)})：*")
            for c in missing_clients:
                lines.append(f"  — {c}")
        if missing_curators:
            lines.append(f"\n⚠️ *Не упомянуты кураторы ({len(missing_curators)})：*")
            for c in missing_curators:
                lines.append(f"  — {c}")
        if warnings:
            lines.append(f"\n⚠️ *Замечания：*")
            for w in warnings:
                lines.append(f"  — {w}")

    report = "\n".join(lines)
    await context.bot.send_message(NOTIFY_CHAT_ID, report, parse_mode="Markdown")

# ─── КОМАНДА /summary ────────────────────────────────────────
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != NOTIFY_CHAT_ID:
        return

    import datetime
    now = datetime.datetime.now(MOSCOW_TZ)
    date_str = now.strftime("%d.%m %H:%M")
    period = "утро" if now.hour < 14 else "вечер"

    submitted = [v["name"] for v in checklist_status.values() if v.get("morning" if period == "утро" else "evening")]
    not_submitted = [v["name"] for v in checklist_status.values() if not v.get("morning" if period == "утро" else "evening")]

    total = len(checklist_status)
    lines = [
        f"📊 Сводка на {date_str} ({period})",
        f"✅ Сдали: {len(submitted)}/{total}",
    ]
    if not_submitted:
        lines.append(f"\n❌ Не сдали ({len(not_submitted)}):")
        for n in not_submitted:
            lines.append(f"  — {n}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ─── АВТОСВОДКА ──────────────────────────────────────────────
async def auto_summary(context: ContextTypes.DEFAULT_TYPE):
    now = context.job.data["time_label"]
    period = "утро" if "утро" in now else "вечер"
    key = "morning" if period == "утро" else "evening"

    submitted = [v["name"] for v in checklist_status.values() if v.get(key)]
    not_submitted = [v["name"] for v in checklist_status.values() if not v.get(key)]
    total = len(checklist_status)

    import datetime
    date_str = datetime.datetime.now(MOSCOW_TZ).strftime("%d.%m %H:%M")

    lines = [
        f"📊 Автосводка на {date_str} ({period})",
        f"✅ Сдали: {len(submitted)}/{total}",
    ]
    if not_submitted:
        lines.append(f"\n❌ Не сдали ({len(not_submitted)}):")
        for n in not_submitted:
            lines.append(f"  — {n}")

    await context.bot.send_message(NOTIFY_CHAT_ID, "\n".join(lines), parse_mode="Markdown")

# ─── ЗАПУСК ──────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("summary", summary))

    # Автосводка в 10:35 и 19:05 по МСК
    job_queue = app.job_queue
    job_queue.run_daily(
        auto_summary,
        time=time(7, 35, tzinfo=MOSCOW_TZ),  # 10:35 МСК = 07:35 UTC
        data={"time_label": "утро"}
    )
    job_queue.run_daily(
        auto_summary,
        time=time(16, 5, tzinfo=MOSCOW_TZ),  # 19:05 МСК = 16:05 UTC
        data={"time_label": "вечер"}
    )

    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
