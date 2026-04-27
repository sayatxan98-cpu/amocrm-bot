from flask import Flask, request
import urllib.request
import urllib.parse
import json, datetime, threading, time, os

app = Flask(__name__)

AMO_DOMAIN        = os.getenv("AMO_DOMAIN", "")
AMO_TOKEN         = os.getenv("AMO_TOKEN", "")
WAZZUP_API_KEY    = os.getenv("WAZZUP_API_KEY", "")
WAZZUP_CHANNEL_ID = os.getenv("WAZZUP_CHANNEL_ID", "")

MANAGERS = [
    {"amo_id": int(os.getenv("MGR1_AMO_ID", "0"))},
    {"amo_id": int(os.getenv("MGR2_AMO_ID", "0"))},
    {"amo_id": int(os.getenv("MGR3_AMO_ID", "0"))},
    {"amo_id": int(os.getenv("MGR4_AMO_ID", "0"))},
    {"amo_id": int(os.getenv("MGR5_AMO_ID", "0"))},
]

STAGE_NEW_LEAD  = os.getenv("STAGE_NEW_LEAD")
STAGE_NO_ANSWER = os.getenv("STAGE_NO_ANSWER")
STAGE_CALLBACK  = os.getenv("STAGE_CALLBACK")
STAGE_IN_WORK   = os.getenv("STAGE_IN_WORK")
STAGE_DECIDING  = os.getenv("STAGE_DECIDING")
STAGE_CONTRACT  = os.getenv("STAGE_CONTRACT")
STAGE_SUCCESS   = os.getenv("STAGE_SUCCESS")

_rr_lock  = threading.Lock()
_rr_index = 0


def http(method, url, data=None, headers=None):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}


def amo(method, path, data=None):
    return http(method, f"https://{AMO_DOMAIN}{path}", data,
                {"Authorization": f"Bearer {AMO_TOKEN}"})


def next_manager():
    global _rr_index
    with _rr_lock:
        m = MANAGERS[_rr_index % len(MANAGERS)]
        _rr_index += 1
    return m


def get_lead(lead_id):
    return amo("GET", f"/api/v4/leads/{lead_id}?with=contacts")


def get_phone(lead_data):
    try:
        cid = lead_data["_embedded"]["contacts"][0]["id"]
        c = amo("GET", f"/api/v4/contacts/{cid}")
        for f in c.get("custom_fields_values") or []:
            if f["field_code"] == "PHONE":
                return "".join(x for x in f["values"][0]["value"] if x.isdigit())
    except Exception:
        pass
    return None


def create_task(lead_id, user_id, text, minutes):
    due = int((datetime.datetime.now() + datetime.timedelta(minutes=minutes)).timestamp())
    amo("POST", "/api/v4/tasks", [{
        "task_type_id": 1,
        "text": text,
        "complete_till": due,
        "entity_id": lead_id,
        "entity_type": "leads",
        "responsible_user_id": user_id,
    }])


def assign_lead(lead_id, user_id):
    amo("PATCH", "/api/v4/leads", [{"id": lead_id, "responsible_user_id": user_id}])


def send_wa(phone, text):
    if not phone:
        return
    http("POST", "https://api.wazzup24.com/v3/message", {
        "channelId": WAZZUP_CHANNEL_ID,
        "chatType": "whatsapp",
        "chatId": phone,
        "text": text,
    }, {"Authorization": f"Bearer {WAZZUP_API_KEY}"})


def later_wa(sec, phone, text):
    def _f():
        time.sleep(sec)
        send_wa(phone, text)
    threading.Thread(target=_f, daemon=True).start()


def on_new_lead(lead_id, d):
    m = next_manager()
    p = get_phone(d)
    assign_lead(lead_id, m["amo_id"])
    create_task(lead_id, m["amo_id"], "Call new lead - 5 min", 5)
    send_wa(p, "Hello! We received your request. Our manager will call you back shortly.")


def on_no_answer(lead_id, d):
    u = d.get("responsible_user_id")
    p = get_phone(d)
    create_task(lead_id, u, "Call back - no answer - 2h", 120)
    send_wa(p, "Hello! We tried to reach you. Please let us know a convenient time to call back!")


def on_callback_later(lead_id, d, mins=60):
    u = d.get("responsible_user_id")
    p = get_phone(d)
    create_task(lead_id, u, "Call back - client asked later", mins)
    if mins > 15:
        later_wa((mins - 15) * 60, p, "Reminder: our manager will call you in about 15 minutes.")


def on_in_work(lead_id, d):
    create_task(lead_id, d.get("responsible_user_id"), "Send commercial offer - 1h", 60)


def on_deciding(lead_id, d):
    u = d.get("responsible_user_id")
    p = get_phone(d)
    create_task(lead_id, u, "Follow up on offer - 1 day", 1440)
    later_wa(7200, p, "Hello! We sent you our commercial offer. Any questions? Feel free to reach out!")


def on_contract(lead_id, d):
    u = d.get("responsible_user_id")
    p = get_phone(d)
    create_task(lead_id, u, "Check contract signing - 1 day", 1440)
    send_wa(p, "The contract has been sent for your review. Let us know if you have any questions!")


def on_success(lead_id, d):
    u = d.get("responsible_user_id")
    p = get_phone(d)
    create_task(lead_id, u, "Request review - 7 days", 10080)
    later_wa(259200, p,
        "Hello! Hope everything went great!\n\n"
        "We have a bonus for you: recommend us to a friend and get a gift from our company!"
    )


STAGE_MAP = {
    STAGE_NO_ANSWER: on_no_answer,
    STAGE_CALLBACK:  on_callback_later,
    STAGE_IN_WORK:   on_in_work,
    STAGE_DECIDING:  on_deciding,
    STAGE_CONTRACT:  on_contract,
    STAGE_SUCCESS:   on_success,
}


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.form.to_dict(flat=False)
    ids = data.get("leads[add][0][id]")
    if ids:
        lid = int(ids[0])
        ld = get_lead(lid)
        if ld:
            threading.Thread(target=on_new_lead, args=(lid, ld), daemon=True).start()
        return "ok", 200
    ir = data.get("leads[status][0][id]")
    sr = data.get("leads[status][0][status_id]")
    if ir and sr:
        lid = int(ir[0])
        h = STAGE_MAP.get(sr[0])
        if h:
            ld = get_lead(lid)
            if ld:
                threading.Thread(target=h, args=(lid, ld), daemon=True).start()
    return "ok", 200


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
