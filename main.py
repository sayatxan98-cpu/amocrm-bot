"""
AmoCRM Automation — Полная автоматизация воронки
Этапы: Новый лид → Не дозвонились → Перезвонить позже →
       В работе → Принимает решение → Согласование договора → Успешно реализовано
"""

from flask import Flask, request
import requests
import datetime
import threading
import time
import os

app = Flask(__name__)

# ============================================================
#  КОНФИГУРАЦИЯ — заполните в файле .env
# ============================================================

AMO_DOMAIN        = os.getenv("AMO_DOMAIN")         # yourcompany.amocrm.ru
AMO_TOKEN         = os.getenv("AMO_TOKEN")           # OAuth токен AmoCRM

WAZZUP_API_KEY    = os.getenv("WAZZUP_API_KEY")      # API ключ Wazzup
WAZZUP_CHANNEL_ID = os.getenv("WAZZUP_CHANNEL_ID")   # ID канала WhatsApp в Wazzup

# ── 5 менеджеров ──────────────────────────────────────────
# amo_id — ID пользователя в AmoCRM (Настройки → Пользователи)
MANAGERS = [
    {"name": "Менеджер 1", "amo_id": int(os.getenv("MGR1_AMO_ID", "0"))},
    {"name": "Менеджер 2", "amo_id": int(os.getenv("MGR2_AMO_ID", "0"))},
    {"name": "Менеджер 3", "amo_id": int(os.getenv("MGR3_AMO_ID", "0"))},
    {"name": "Менеджер 4", "amo_id": int(os.getenv("MGR4_AMO_ID", "0"))},
    {"name": "Менеджер 5", "amo_id": int(os.getenv("MGR5_AMO_ID", "0"))},
]

# ── ID этапов воронки ─────────────────────────────────────
# Как найти: AmoCRM → Настройки → Воронки → смотрите URL при наведении на этап
STAGE_NEW_LEAD   = os.getenv("STAGE_NEW_LEAD")    # Новый лид
STAGE_NO_ANSWER  = os.getenv("STAGE_NO_ANSWER")   # Не дозвонились
STAGE_CALLBACK   = os.getenv("STAGE_CALLBACK")    # Перезвонить позже
STAGE_IN_WORK    = os.getenv("STAGE_IN_WORK")     # В работе
STAGE_DECIDING   = os.getenv("STAGE_DECIDING")    # Принимает решение
STAGE_CONTRACT   = os.getenv("STAGE_CONTRACT")    # Согласование договора
STAGE_SUCCESS    = os.getenv("STAGE_SUCCESS")     # Успешно реализовано

# ── Счётчик round-robin ───────────────────────────────────
_rr_lock  = threading.Lock()
_rr_index = 0


# ============================================================
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def amo_headers():
    return {
        "Authorization": f"Bearer {AMO_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }

def next_manager():
    """Round-robin: возвращает следующего менеджера по очереди."""
    global _rr_index
    with _rr_lock:
        mgr = MANAGERS[_rr_index % len(MANAGERS)]
        _rr_index += 1
    return mgr

def get_lead(lead_id):
    """Получить данные сделки вместе с контактами."""
    r = requests.get(
        f"https://{AMO_DOMAIN}/api/v4/leads/{lead_id}?with=contacts",
        headers=amo_headers(), timeout=10,
    )
    return r.json() if r.ok else None

def get_phone(lead_data):
    """Извлечь номер телефона из сделки (первый контакт)."""
    try:
        contacts = lead_data["_embedded"]["contacts"]
        if not contacts:
            return None
        contact_id = contacts[0]["id"]
        r = requests.get(
            f"https://{AMO_DOMAIN}/api/v4/contacts/{contact_id}",
            headers=amo_headers(), timeout=10,
        )
        if not r.ok:
            return None
        for field in r.json().get("custom_fields_values") or []:
            if field["field_code"] == "PHONE":
                raw = field["values"][0]["value"]
                return "".join(c for c in raw if c.isdigit())
    except Exception:
        pass
    return None

def create_task(lead_id, user_id, text, minutes, task_type=1):
    """
    Создать задачу в AmoCRM.
    task_type: 1 = Звонок, 2 = Встреча, 3 = Написать письмо
    """
    due = int((datetime.datetime.now() + datetime.timedelta(minutes=minutes)).timestamp())
    requests.post(
        f"https://{AMO_DOMAIN}/api/v4/tasks",
        json=[{
            "task_type_id": task_type,
            "text": text,
            "complete_till": due,
            "entity_id": lead_id,
            "entity_type": "leads",
            "responsible_user_id": user_id,
        }],
        headers=amo_headers(), timeout=10,
    )

def assign_lead(lead_id, user_id):
    """Назначить ответственного по сделке."""
    requests.patch(
        f"https://{AMO_DOMAIN}/api/v4/leads",
        json=[{"id": lead_id, "responsible_user_id": user_id}],
        headers=amo_headers(), timeout=10,
    )

def whatsapp(phone, text):
    """Отправить WhatsApp сообщение через Wazzup."""
    if not phone:
        return
    requests.post(
        "https://api.wazzup24.com/v3/message",
        json={
            "channelId": WAZZUP_CHANNEL_ID,
            "chatType": "whatsapp",
            "chatId": phone,
            "text": text,
        },
        headers={
            "Authorization": f"Bearer {WAZZUP_API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
        },
        timeout=10,
    )

def delayed_whatsapp(seconds, phone, text):
    """Отправить WhatsApp через N секунд (не блокирует основной поток)."""
    def _send():
        time.sleep(seconds)
        whatsapp(phone, text)
    threading.Thread(target=_send, daemon=True).start()


# ============================================================
#  ОБРАБОТЧИКИ ЭТАПОВ
# ============================================================

def on_new_lead(lead_id, lead_data):
    """
    НОВЫЙ ЛИД
    - Round-robin распределение по менеджерам
    - Задача «Позвонить» через 5 минут
    - WhatsApp клиенту — приветствие
    """
    mgr   = next_manager()
    phone = get_phone(lead_data)

    assign_lead(lead_id, mgr["amo_id"])
    create_task(lead_id, mgr["amo_id"], "Позвонить новому лиду", minutes=5)

    whatsapp(phone,
        "Здравствуйте!\n"
        "Мы получили вашу заявку. Наш менеджер перезвонит вам в течение нескольких минут."
    )


def on_no_answer(lead_id, lead_data):
    """
    НЕ ДОЗВОНИЛИСЬ
    - Задача «Перезвонить» через 2 часа
    - WhatsApp клиенту — мягкое сообщение
    """
    user_id = lead_data.get("responsible_user_id")
    phone   = get_phone(lead_data)

    create_task(lead_id, user_id, "Перезвонить (не дозвонились)", minutes=120)

    whatsapp(phone,
        "Здравствуйте! Пытались до вас дозвониться.\n"
        "Напишите удобное время — перезвоним в любое время!"
    )


def on_callback_later(lead_id, lead_data, callback_minutes=60):
    """
    ПЕРЕЗВОНИТЬ ПОЗЖЕ
    - Задача на указанное время (по умолчанию через 1 час)
    - Напоминание клиенту в WhatsApp за 15 минут до звонка
    """
    user_id = lead_data.get("responsible_user_id")
    phone   = get_phone(lead_data)

    create_task(lead_id, user_id, "Перезвонить — клиент просил позже", minutes=callback_minutes)

    if callback_minutes > 15:
        delayed_whatsapp((callback_minutes - 15) * 60, phone,
            "Напоминаем — наш менеджер перезвонит вам примерно через 15 минут."
        )


def on_in_work(lead_id, lead_data):
    """
    В РАБОТЕ (контакт установлен)
    - Задача «Отправить КП» через 1 час
    """
    user_id = lead_data.get("responsible_user_id")
    create_task(lead_id, user_id, "Отправить коммерческое предложение", minutes=60)


def on_deciding(lead_id, lead_data):
    """
    ПРИНИМАЕТ РЕШЕНИЕ
    - Задача «Уточнить решение» через 1 день
    - WhatsApp клиенту через 2 часа — follow-up
    """
    user_id = lead_data.get("responsible_user_id")
    phone   = get_phone(lead_data)

    create_task(lead_id, user_id, "Уточнить решение по КП", minutes=1440)

    delayed_whatsapp(7200, phone,
        "Добрый день!\n"
        "Направили вам коммерческое предложение.\n"
        "Если есть вопросы — пишите или звоните, всегда рады помочь!"
    )


def on_contract(lead_id, lead_data):
    """
    СОГЛАСОВАНИЕ ДОГОВОРА
    - Задача «Проверить подписание» через 1 день
    - WhatsApp клиенту — уведомление об отправке договора
    """
    user_id = lead_data.get("responsible_user_id")
    phone   = get_phone(lead_data)

    create_task(lead_id, user_id, "Проверить подписание договора", minutes=1440)

    whatsapp(phone,
        "Договор направлен вам на согласование.\n"
        "Если есть правки или вопросы — сообщите нам, всё оперативно исправим!"
    )


def on_success(lead_id, lead_data):
    """
    УСПЕШНО РЕАЛИЗОВАНО
    - Задача «Запросить отзыв» через 7 дней
    - WhatsApp клиенту через 3 дня — реферальная программа
    """
    user_id = lead_data.get("responsible_user_id")
    phone   = get_phone(lead_data)

    create_task(lead_id, user_id, "Запросить отзыв от клиента", minutes=10080)

    delayed_whatsapp(259200, phone,
        "Добрый день! Надеемся, всё прошло отлично!\n\n"
        "У нас есть приятный бонус:\n"
        "Порекомендуйте нас другу — и получите подарок от компании!\n\n"
        "Просто передайте другу наш контакт и скажите что от вас."
    )


# ============================================================
#  WEBHOOK — ТОЧКА ВХОДА
# ============================================================

STAGE_MAP = {
    STAGE_NO_ANSWER : on_no_answer,
    STAGE_CALLBACK  : on_callback_later,
    STAGE_IN_WORK   : on_in_work,
    STAGE_DECIDING  : on_deciding,
    STAGE_CONTRACT  : on_contract,
    STAGE_SUCCESS   : on_success,
}


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.form.to_dict(flat=False)

    # Новая сделка создана
    lead_ids = data.get("leads[add][0][id]")
    if lead_ids:
        lead_id   = int(lead_ids[0])
        lead_data = get_lead(lead_id)
        if lead_data:
            threading.Thread(
                target=on_new_lead, args=(lead_id, lead_data), daemon=True
            ).start()
        return "ok", 200

    # Смена этапа
    id_raw     = data.get("leads[status][0][id]")
    status_raw = data.get("leads[status][0][status_id]")

    if id_raw and status_raw:
        lead_id   = int(id_raw[0])
        status_id = status_raw[0]
        handler   = STAGE_MAP.get(status_id)

        if handler:
            lead_data = get_lead(lead_id)
            if lead_data:
                threading.Thread(
                    target=handler, args=(lead_id, lead_data), daemon=True
                ).start()

    return "ok", 200


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
