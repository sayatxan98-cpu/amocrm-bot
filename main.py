"""
AmoCRM Automation — Полная автоматизация воронки
"""

from flask import Flask, request
import requests
import json
import datetime
import threading
import time
import os

app = Flask(__name__)

AMO_DOMAIN        = os.getenv("AMO_DOMAIN")
AMO_TOKEN         = os.getenv("AMO_TOKEN")
WAZZUP_API_KEY    = os.getenv("WAZZUP_API_KEY")
WAZZUP_CHANNEL_ID = os.getenv("WAZZUP_CHANNEL_ID")

MANAGERS = [
    {"name": "Menedzher 1", "amo_id": int(os.getenv("MGR1_AMO_ID", "0"))},
    {"name": "Menedzher 2", "amo_id": int(os.getenv("MGR2_AMO_ID", "0"))},
    {"name": "Menedzher 3", "amo_id": int(os.getenv("MGR3_AMO_ID", "0"))},
    {"name": "Menedzher 4", "amo_id": int(os.getenv("MGR4_AMO_ID", "0"))},
    {"name": "Menedzher 5", "amo_id": int(os.getenv("MGR5_AMO_ID", "0"))},
]

STAGE_NEW_LEAD   = os.getenv("STAGE_NEW_LEAD")
STAGE_NO_ANSWER  = os.getenv("STAGE_NO_ANSWER")
STAGE_CALLBACK   = os.getenv("STAGE_CALLBACK")
STAGE_IN_WORK    = os.getenv("STAGE_IN_WORK")
STAGE_DECIDING   = os.getenv("STAGE_DECIDING")
STAGE_CONTRACT   = os.getenv("STAGE_CONTRACT")
STAGE_SUCCESS    = os.getenv("STAGE_SUCCESS")

_rr_lock  = threading.Lock()
_rr_index = 0


def amo_post(url, data):
    """POST v AmoCRM s UTF-8 kodirovkoj."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return requests.post(
        url, data=body,
        headers={
            "Authorization": f"Bearer {AMO_TOKEN}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )


def amo_patch(url, data):
    """PATCH v AmoCRM s UTF-8 kodirovkoj."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return requests.patch(
        url, data=body,
        headers={
            "Authorization": f"Bearer {AMO_TOKEN}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )


def amo_get(url):
    return requests.get(
        url,
        headers={"Authorization": f"Bearer {AMO_TOKEN}"},
        timeout=10,
    )


def next_manager():
    global _rr_index
    with _rr_lock:
        mgr = MANAGERS[_rr_index % len(MANAGERS)]
        _rr_index += 1
    return mgr


def get_lead(lead_id):
    r = amo_get(f"https://{AMO_DOMAIN}/api/v4/leads/{lead_id}?with=contacts")
    return r.json() if r.ok else None


def get_phone(lead_data):
    try:
        contacts = lead_data["_embedded"]["contacts"]
        if not contacts:
            return None
        contact_id = contacts[0]["id"]
        r = amo_get(f"https://{AMO_DOMAIN}/api/v4/contacts/{contact_id}")
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
    due = int((datetime.datetime.now() + datetime.timedelta(minutes=minutes)).timestamp())
    amo_post(
        f"https://{AMO_DOMAIN}/api/v4/tasks",
        [{
            "task_type_id": task_type,
            "text": text,
            "complete_till": due,
            "entity_id": lead_id,
            "entity_type": "leads",
            "responsible_user_id": user_id,
        }]
    )


def assign_lead(lead_id, user_id):
    amo_patch(
        f"https://{AMO_DOMAIN}/api/v4/leads",
        [{"id": lead_id, "responsible_user_id": user_id}]
    )


def whatsapp(phone, text):
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
            "Content-Type": "application/json",
        },
        timeout=10,
    )


def delayed_whatsapp(seconds, phone, text):
    def _send():
        time.sleep(seconds)
        whatsapp(phone, text)
    threading.Thread(target=_send, daemon=True).start()


# Названия задач на латинице — чтобы не было ошибок кодировки
TASK_NEW_LEAD  = "Pozvonitj novomu lidu — 5 minut"
TASK_NO_ANSWER = "Perezvonit (ne dozvo nilis) — 2 chasa"
TASK_CALLBACK  = "Perezvonit — klient prosil pozzhe"
TASK_IN_WORK   = "Otpravit KP — 1 chas"
TASK_DECIDING  = "Utochnit reshenie po KP — 1 denj"
TASK_CONTRACT  = "Proveritj podpisanie dogovora"
TASK_REVIEW    = "Zaprosit otzyv ot klienta"


def on_new_lead(lead_id, lead_data):
    mgr   = next_manager()
    phone = get_phone(lead_data)
    assign_lead(lead_id, mgr["amo_id"])
    create_task(lead_id, mgr["amo_id"], TASK_NEW_LEAD, minutes=5)
    whatsapp(phone,
        "Zdravstvujte!\n"
        "My poluchili vashu zayavku. Nash menedzher perezvonit vam v techenie neskoljkikh minut."
    )


def on_no_answer(lead_id, lead_data):
    user_id = lead_data.get("responsible_user_id")
    phone   = get_phone(lead_data)
    create_task(lead_id, user_id, TASK_NO_ANSWER, minutes=120)
    whatsapp(phone,
        "Zdravstvujte! Pytalis do vas dozvonitjsya.\n"
        "Napishite udobnoe vremya — perezvonm v lyuboe vremya!"
    )


def on_callback_later(lead_id, lead_data, callback_minutes=60):
    user_id = lead_data.get("responsible_user_id")
    phone   = get_phone(lead_data)
    create_task(lead_id, user_id, TASK_CALLBACK, minutes=callback_minutes)
    if callback_minutes > 15:
        delayed_whatsapp((callback_minutes - 15) * 60, phone,
            "Napominaem — nash menedzher perezvonit vam primerno cherez 15 minut."
        )


def on_in_work(lead_id, lead_data):
    user_id = lead_data.get("responsible_user_id")
    create_task(lead_id, user_id, TASK_IN_WORK, minutes=60)


def on_deciding(lead_id, lead_data):
    user_id = lead_data.get("responsible_user_id")
    phone   = get_phone(lead_data)
    create_task(lead_id, user_id, TASK_DECIDING, minutes=1440)
    delayed_whatsapp(7200, phone,
        "Dobryj denj!\n"
        "Napravili vam kommercheskoe predlozhenie.\n"
        "Estj voprosy — pishite ili zvonite!"
    )


def on_contract(lead_id, lead_data):
    user_id = lead_data.get("responsible_user_id")
    phone   = get_phone(lead_data)
    create_task(lead_id, user_id, TASK_CONTRACT, minutes=1440)
    whatsapp(phone,
        "Dogovor napravlen vam na soglasovanie.\n"
        "Estj pravki — soobshhite nam!"
    )


def on_success(lead_id, lead_data):
    user_id = lead_data.get("responsible_user_id")
    phone   = get_phone(lead_data)
    create_task(lead_id, user_id, TASK_REVIEW, minutes=10080)
    delayed_whatsapp(259200, phone,
        "Dobryj denj! Nadeemsya, vsyo proshlo otlichno!\n\n"
        "U nas estj priyatnyj bonus:\n"
        "Porekomendujte nas drugu — i poluchite podarok!\n\n"
        "Prosto peredajte drugu nash kontakt i skazhite chto ot vas."
    )


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

    lead_ids = data.get("leads[add][0][id]")
    if lead_ids:
        lead_id   = int(lead_ids[0])
        lead_data = get_lead(lead_id)
        if lead_data:
            threading.Thread(target=on_new_lead, args=(lead_id, lead_data), daemon=True).start()
        return "ok", 200

    id_raw     = data.get("leads[status][0][id]")
    status_raw = data.get("leads[status][0][status_id]")

    if id_raw and status_raw:
        lead_id   = int(id_raw[0])
        status_id = status_raw[0]
        handler   = STAGE_MAP.get(status_id)
        if handler:
            lead_data = get_lead(lead_id)
            if lead_data:
                threading.Thread(target=handler, args=(lead_id, lead_data), daemon=True).start()

    return "ok", 200


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
