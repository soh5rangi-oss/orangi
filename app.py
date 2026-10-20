import calendar
import json
import os
import sqlite3
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from functools import wraps

import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY") or "not-set")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

BACKEND_URL = "https://baby-monitor-backend.onrender.com"

SENSOR_LATEST_API_URL = (
    f"{BACKEND_URL}/api/sensor/latest"
)

CAMERA_STREAM_API_URL = (
    f"{BACKEND_URL}/api/video/stream"
)

AWS_RISK_API_URL = (
    "https://41ak752nn6.execute-api."
    "ap-northeast-3.amazonaws.com/prod/risk"
)

def backend_signup(email, password, name=None, phone=None, birthdate=None):
    fields = {"email": (None, email), "password": (None, password)}
    if name:
        fields["name"] = (None, name)
    if phone:
        fields["phone"] = (None, phone)
    if birthdate:
        fields["birthdate"] = (None, birthdate)
    return requests.post(
        f"{BACKEND_URL}/api/auth/signup",
        files=fields,
        timeout=90
    )


def backend_login(email, password):
    return requests.post(
        f"{BACKEND_URL}/api/auth/login",
        data={"username": email, "password": password},
        timeout=90
    )


def temp_percent(value):
    return max(8, min(100, (value - 34) / 6 * 100))


def build_temp_series(values):
    labels = ["0", "10", "20", "30", "40", "50"]
    return [
        {"label": lbl, "value": val, "percent": temp_percent(val)}
        for lbl, val in zip(labels, values)
    ]

camera = {
    "face_visible": True,
    "movement": "정상",
    "blanket": False,
    "temperature": 36.5,
    "breath": "정상",
    "sleep_state": "정상 수면 중",
    "posture": "정자세 유지"
}
LLM_INTERVAL_SECONDS = 30

last_llm_run_at = 0
latest_llm_result = None

last_levels = {"temp": "normal", "risk": "normal"}

NOTIFICATION_SETTINGS = {
    "general": True, "sound": True, "vibrate": True,
    "sleep_alert": False, "temp_alert": False
}

TEMP_HISTORY_LABELS = ["-1hr", "-50m", "-40m", "-30m", "-20m", "-10m"]
TEMP_HISTORY = deque([36.4, 36.5, 36.6, 36.5, 36.6, 36.5], maxlen=6)

NOTIFICATIONS = [
    {"group": "오늘", "icon": "🌡️", "title": "체온 상승 중",
     "desc": "현재 유아의 체온이 상승중입니다.", "time": "2:00 - April 24"},
    {"group": "오늘", "icon": "🛌", "title": "얼굴 상태 주의",
     "desc": "현재 유아의 얼굴에 이불이 덮혀있습니다.", "time": "21:00 - April 24"},
    {"group": "어제", "icon": "📝", "title": "수면기록 업데이트",
     "desc": "오늘의 수면 기록이 업데이트 되었습니다.", "time": "10:00 - April 23"},
    {"group": "어제", "icon": "🌡️", "title": "체온 상승 중",
     "desc": "현재 유아의 체온이 상승중입니다.", "time": "3:00 - April 23"},
    {"group": "이번주", "icon": "🛌", "title": "호흡 주의",
     "desc": "현재 유아의 호흡이 불안정합니다.", "time": "23:00 - April 22"},
]

SLEEP_SLOTS = [
    "9:00 PM", "10:00 PM", "11:00 PM", "12:00 PM",
    "1:00 AM", "2:00 AM", "3:00 AM", "4:00 AM",
    "5:00 AM", "6:00 AM", "7:00 AM", "8:00 AM"
]

_DEFAULT_SLOT = {
    "breath": "정상", "sleep_state": "정상 수면 중", "posture": "정자세 유지",
    "face_state": "정상", "movement": "거의 없음",
    "risk_level": "normal", "risk_text": "정상"
}

SLEEP_HISTORY = {slot: dict(_DEFAULT_SLOT) for slot in SLEEP_SLOTS}
SLEEP_HISTORY["11:00 PM"] = {
    "breath": "불안정", "sleep_state": "심한 뒤척임", "posture": "자세 변동 많음",
    "face_state": "이불이 얼굴 전체를 덮음", "movement": "활발",
    "risk_level": "danger", "risk_text": "위험"
}
SLEEP_HISTORY["1:00 AM"] = {
    "breath": "약간 불안정", "sleep_state": "자주 뒤척임", "posture": "옆으로 누움",
    "face_state": "이불이 얼굴 근처에 있음", "movement": "보통",
    "risk_level": "caution", "risk_text": "주의"
}
SLEEP_HISTORY["3:00 AM"] = {
    "breath": "정상", "sleep_state": "12초 뒤척임 후 안정", "posture": "정자세 유지",
    "face_state": "이불이 얼굴 근처에 있음", "movement": "뒤척임 3회",
    "risk_level": "normal", "risk_text": "정상"
}

_SLOT_TEMPS = {
    "11:00 PM": [37.0, 37.3, 37.6, 37.9, 38.2, 38.5],
    "1:00 AM": [36.8, 36.9, 37.0, 37.1, 37.2, 37.1],
    "3:00 AM": [36.3, 36.5, 36.4, 36.6, 36.5, 36.4],
}
for _slot in SLEEP_SLOTS:
    SLEEP_HISTORY[_slot]["temp_series"] = build_temp_series(
        _SLOT_TEMPS.get(_slot, [36.5, 36.5, 36.5, 36.5, 36.5, 36.5])
    )

FIRST_AID_ITEMS = [
    {"slug": "airway", "icon": "😖", "title": "기도 막힘 / 질식"},
    {"slug": "fever", "icon": "🌡️", "title": "열 / 발열"},
    {"slug": "allergy", "icon": "🤚", "title": "알레르기"},
    {"slug": "fall", "icon": "🤕", "title": "낙상 / 머리 부딪힘"},
    {"slug": "breathing", "icon": "😮‍💨", "title": "호흡이상 / 무호흡"},
    {"slug": "cpr", "icon": "🫀", "title": "심폐소생술"},
]

FIRST_AID_DATA = {
    "airway": {
        "title": "기도 막힘 / 질식",
        "symptoms": ["기침을 못 하거나 소리가 나지 않음", "얼굴이 파래짐 (청색증)"],
        "steps": [
            "상태 확인",
            "강하게 기침하도록 유도",
            "기침을 못 하면 119 신고",
            "성인/1세 이상 소아: 등을 5회 강하게 두드린 후 효과 없으면 하임리히법 반복",
            "의식을 잃으면 → CPR 시작"
        ],
        "note": "영아(1세 미만)는 하임리히법 금지"
    },
    "fever": {
        "title": "열 / 발열",
        "symptoms": ["체온 상승 (보통 38℃ 이상)", "얼굴이 붉음", "몸이 뜨겁고 땀이 남"],
        "steps": [
            "옷을 느슨하게 하기",
            "미지근한 물수건으로 몸 닦기",
            "필요 시 해열제 사용",
            "체온과 상태를 계속 관찰"
        ],
        "note": "40℃ 이상 고열, 경련 발생, 호흡이 힘든 경우 즉시 병원 방문/119"
    },
    "allergy": {
        "title": "알레르기",
        "symptoms": ["두드러기", "피부 가려움", "얼굴・입술 부종", "호흡 곤란"],
        "steps": [
            "편한 자세로 안정시키기",
            "증상 관찰",
            "처방받은 에피네프린 자동주사가 있다면 사용",
            "가라앉지 않을 시 119 신고"
        ],
        "note": None
    },
    "fall": {
        "title": "낙상 / 머리 부딪힘",
        "symptoms": None,
        "steps": [
            "움직이지 않게 안정",
            "부딪힌 부위는 차가운 팩으로 냉찜질 (15~20분)",
            "출혈 시 깨끗한 천으로 압박",
            "24시간 이상 상태 관찰"
        ],
        "note": None
    },
    "breathing": {
        "title": "호흡이상 / 무호흡",
        "symptoms": None,
        "steps": [
            "반응 확인",
            "가슴 움직임으로 호흡 확인 (10초 이내)",
            "호흡이 없거나 비정상적이면 119 신고",
            "CPR 준비 및 시작",
            "자동심장충격기(AED)가 있으면 사용"
        ],
        "note": "숨이 가빠도 의식이 있음 → 편한 자세(앉은 자세)로 안정시키고 도움 요청"
    },
    "cpr": {
        "title": "심폐소생술",
        "symptoms": None,
        "steps": [
            "어깨를 두드리며 반응 확인",
            "119 신고 & AED 요청",
            "가슴 압박\n  • 가슴 중앙을 압박\n  • 속도: 분당 100~120회\n  • 깊이:\n    - 성인 약 5~6cm\n    - 소아: 가슴 두께의 약 1/3",
            "AED 사용\n  • 전원을 켜고 음성 안내에 따르기",
            "구조대 도착까지 계속 반복"
        ],
        "note": None
    },
}


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            birthdate TEXT,
            password_hash TEXT NOT NULL,
            photo TEXT
        )
    """)
    db.commit()
    db.close()

def fetch_aws_risk(sensor_values):
    object_temp = sensor_values.get("object_temp")
    breathing_rate = sensor_values.get("breathing_rate", 30.0)
    breath_status = sensor_values.get(
        "breathing_status",
        "정상"
    )
    sensor_state = sensor_values.get("raw_sensor_state")

    movement_text = camera.get("movement", "정상")
    if movement_text in ["거의 없음", "없음"]:
        movement_value = "very low"
    elif movement_text in ["적음", "낮음"]:
        movement_value = "low"
    else:
        movement_value = "normal"

    face_text = describe_face_state(camera)
    posture = camera.get("posture", "정보 없음")
    sleep_state = camera.get("sleep_state", "정보 없음")

    situation_text = (
        f"호흡 상태: {breath_status}, "
        f"자세: {posture}, "
        f"얼굴 상태: {face_text}, "
        f"수면 상태: {sleep_state}"
    )

    llm_result = None

    try:
        prompt = f"""
다음 유아 수면 상태를 종합적으로 분석하여 위험도를 판단하세요.

Object Temperature: {object_temp}도
호흡 상태: {breath_status}
호흡수: {breathing_rate}
호흡센서 State 원본값: {sensor_state}
수면 상태: {sleep_state}
자세: {posture}
얼굴 상태: {face_text}
움직임: {movement_text}

Object Temperature는 보정되지 않은 표면 온도이며, 시연에서는 35~37도를 정상으로 판단하세요.
호흡수 30은 시연용 정상값으로 판단하세요.

반드시 다음 JSON 형식으로만 응답하세요.

{{
  "risk_level": "safe, caution, danger 중 하나",
  "risk_label": "안전, 주의, 위험 중 하나",
  "color": "green, yellow, red 중 하나",
  "reasons": ["판단 근거"],
  "risk_text": "현재 상태를 설명하는 한 문장",
  "recommended_action": "보호자 권장 조치"
}}
"""

        ai_response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "너는 유아 수면 안전 상태를 분석하는 보조 시스템이다. "
                        "센서와 영상 분석 정보를 종합하여 보수적으로 판단한다."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            response_format={"type": "json_object"}
        )

        llm_result = json.loads(
            ai_response.choices[0].message.content
        )

    except Exception:
        llm_result = None

    payload = {
        "temperature": object_temp,
        "breathing_rate": breathing_rate,
        "movement": movement_value,
        "situation_text": situation_text,
        "llm_result": llm_result
    }

    response = requests.post(
        AWS_RISK_API_URL,
        json=payload,
        timeout=30
    )
    response.raise_for_status()

    return response.json().get("risk_result")

def get_risk(camera_state):
    if camera_state.get("blanket"):
        return "danger", "위험"
    if camera_state.get("movement") == "많음":
        return "caution", "주의"
    return "normal", "정상"


def get_current_risk_state():
    risk_level, risk_text = get_risk(camera)
    llm = "센서 데이터 분석을 기다리는 중입니다."

    # 1분마다 자동 분석된 최신 LLM 결과 사용
    aws_result = latest_llm_result

    if aws_result:
        aws_level = aws_result.get("risk_level", "safe")

        level_map = {
            "safe": "normal",
            "caution": "caution",
            "danger": "danger"
        }

        risk_level = level_map.get(aws_level, "normal")
        risk_text = aws_result.get("risk_label", "안전")
        llm = aws_result.get(
            "risk_text",
            "현재 상태를 확인할 수 없습니다."
        )

    return risk_level, risk_text, llm


def get_llm_analysis():
    aws_result = latest_llm_result or {}
    reasons = aws_result.get("reasons") or []
    if isinstance(reasons, str):
        reasons = [reasons]

    return {
        "summary": aws_result.get("risk_text", "센서 데이터 분석을 기다리는 중입니다."),
        "reasons": reasons,
        "action": aws_result.get("recommended_action", "")
    }


def get_temp_level(temp):
    if temp >= 39.0:
        return "high", "고열"
    if temp >= 38.0:
        return "fever", "발열"
    if temp >= 37.5:
        return "mild", "미열"
    return "normal", "정상"


def describe_face_state(camera_state):
    return "이불이 얼굴을 덮음" if camera_state.get("blanket") else "정상"


def add_notification(group, icon, title, desc, time_label):
    NOTIFICATIONS.insert(0, {
        "group": group, "icon": icon, "title": title,
        "desc": desc, "time": time_label
    })


def get_current_user():
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE email = ?", (session["user_email"],)
    ).fetchone()
    db.close()
    return user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "access_token" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/")
def splash():
    return render_template("splash.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")

        try:
            resp = backend_login(identifier, password)
        except requests.exceptions.RequestException:
            flash("서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요")
            return render_template("login.html", identifier=identifier)

        token = resp.json().get("access_token") if resp.ok else None
        if not token:
            flash("이메일 또는 비밀번호가 올바르지 않습니다")
            return render_template("login.html", identifier=identifier)

        session["access_token"] = token
        session["user_email"] = identifier

        db = get_db()
        local_user = db.execute(
            "SELECT * FROM users WHERE email = ?", (identifier,)
        ).fetchone()
        if not local_user:
            db.execute(
                "INSERT INTO users (name, email, phone, birthdate, password_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (identifier, identifier, "", "", generate_password_hash(password))
            )
            db.commit()
            local_user = db.execute(
                "SELECT * FROM users WHERE email = ?", (identifier,)
            ).fetchone()
        db.close()

        session["user_name"] = local_user["name"]
        return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        birthdate = request.form.get("birthdate", "")

        try:
            signup_resp = backend_signup(email, password, name=name, phone=phone, birthdate=birthdate)
        except requests.exceptions.RequestException:
            flash("서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요")
            return render_template(
                "signup.html", name=name, email=email,
                phone=phone, birthdate=birthdate
            )

        if not signup_resp.ok:
            try:
                message = signup_resp.json().get("detail", "회원가입에 실패했습니다")
            except ValueError:
                message = "회원가입에 실패했습니다"
            flash(message)
            return render_template(
                "signup.html", name=name, email=email,
                phone=phone, birthdate=birthdate
            )

        try:
            login_resp = backend_login(email, password)
        except requests.exceptions.RequestException:
            login_resp = None

        token = login_resp.json().get("access_token") if login_resp and login_resp.ok else None
        if not token:
            flash("회원가입은 완료됐지만 자동 로그인에 실패했습니다. 로그인해주세요")
            return redirect(url_for("login"))

        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            db.execute(
                "UPDATE users SET name = ?, phone = ?, birthdate = ?, password_hash = ? "
                "WHERE email = ?",
                (name, phone, birthdate, generate_password_hash(password), email)
            )
        else:
            db.execute(
                "INSERT INTO users (name, email, phone, birthdate, password_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, email, phone, birthdate, generate_password_hash(password))
            )
        db.commit()
        db.close()

        session["access_token"] = token
        session["user_email"] = email
        session["user_name"] = name
        return redirect(url_for("home"))

    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("splash"))


@app.route("/home")
@login_required
def home():
    temp, _, _, _ = get_current_temperature_state()
    risk_level, risk_text, llm = get_current_risk_state()

    return render_template(
        "home.html",
        camera=camera,
        temp=temp,
        llm=llm,
        user_name=session.get("user_name", ""),
        risk_level=risk_level,
        risk_text=risk_text
    )


@app.route("/notifications")
@login_required
def notifications():
    grouped = []
    for group in ["오늘", "어제", "이번주"]:
        items = [n for n in NOTIFICATIONS if n["group"] == group]
        if items:
            grouped.append((group, items))
    return render_template("notifications.html", grouped=grouped)

def fetch_latest_sensor_data():
    token = session.get("access_token")

    if not token:
        return None

    try:
        response = requests.get(
            SENSOR_LATEST_API_URL,
            headers={
                "Authorization": f"Bearer {token}"
            },
            timeout=10
        )

        if not response.ok:
            print(
                "최신 센서 데이터 조회 실패:",
                response.status_code,
                response.text
            )
            return None

        sensor = response.json()
        print("최신 센서 데이터:", sensor)

        return sensor

    except requests.exceptions.RequestException as error:
        print("센서 백엔드 연결 실패:", error)
        return None

def prepare_sensor_values(sensor):
    raw_rpm = sensor.get("breathing_rate")
    raw_state = sensor.get("movement_value")

    return {
        "object_temp": sensor.get("temperature"),

        # 시연용 고정 정상값
        "breathing_rate": 30.0,
        "breathing_status": "정상",

        # 원본값은 확인을 위해 보존
        "raw_breathing_rate": raw_rpm,
        "raw_sensor_state": raw_state,

        "breathing_source": "demo_fallback",
        "breathing_time": sensor.get("breathing_time"),
        "temperature_time": sensor.get("temperature_time")
    }

@app.route("/sensor/latest")
@login_required
def sensor_latest():
    sensor = fetch_latest_sensor_data()

    if sensor is None:
        return {
            "message": "최신 센서 데이터를 가져오지 못했습니다."
        }, 502

    return sensor

def fetch_backend_temperature_logs():
    token = session.get("access_token")
    if not token:
        return None
    try:
        resp = requests.get(
            f"{BACKEND_URL}/api/temperature",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
    except requests.exceptions.RequestException:
        return None
    return resp.json() if resp.ok else None


def bucket_temperature_history(logs, bucket_minutes=10, bucket_count=6):
    """최근 센서 기록을 10분 간격 6개 구간으로 묶는다.

    백엔드 /api/temperature는 센서가 보낸 원본 기록을 그대로 반환하므로
    (집계 없음), 센서 전송 주기가 짧아지면 '최근 1시간' 그래프가 실제로는
    수 초 단위로만 보이는 문제가 있어 여기서 직접 10분 단위로 묶는다.
    """
    # 백엔드가 내려주는 measured_at은 UTC 기준이라 UTC로 맞춰 비교한다.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_start = now - timedelta(minutes=bucket_minutes * bucket_count)

    parsed = []
    for entry in logs or []:
        try:
            measured_at = datetime.fromisoformat(entry["measured_at"])
            parsed.append((measured_at, float(entry["temperature"])))
        except (KeyError, TypeError, ValueError):
            continue
    parsed.sort(key=lambda item: item[0])

    bucket_ends = []
    values = []
    for i in range(bucket_count):
        bucket_start = window_start + timedelta(minutes=bucket_minutes * i)
        bucket_end = bucket_start + timedelta(minutes=bucket_minutes)
        bucket_values = [temp for ts, temp in parsed if bucket_start <= ts < bucket_end]

        bucket_ends.append(bucket_end)
        values.append(bucket_values[-1] if bucket_values else None)

    if all(v is None for v in values):
        return []

    # 데이터가 없는 앞쪽 구간은 뒤에서부터, 중간 공백은 직전 값으로 채운다
    # (센서를 켠 지 얼마 안 됐을 때 그래프가 끊겨 보이지 않도록).
    first_known = next(v for v in values if v is not None)
    carry_forward = first_known
    for i in range(bucket_count):
        if values[i] is None:
            values[i] = carry_forward
        else:
            carry_forward = values[i]

    history = []
    for bucket_end, value in zip(bucket_ends, values):
        # 로컬 표시용 레이블이므로 다시 로컬 시간대로 변환해서 보여준다.
        local_end = bucket_end.replace(tzinfo=timezone.utc).astimezone()
        history.append({
            "label": local_end.strftime("%H:%M"),
            "value": value,
            "percent": temp_percent(value)
        })

    return history


def get_current_temperature_state():
    logs = fetch_backend_temperature_logs()
    history = bucket_temperature_history(logs) if logs else None

    if logs:
        temp = float(logs[-1]["temperature"])
    else:
        temp = camera.get("temperature", 36.5)

    if not history:
        history = [
            {"label": lbl, "value": val, "percent": temp_percent(val)}
            for lbl, val in zip(TEMP_HISTORY_LABELS, TEMP_HISTORY)
        ]

    level, label = get_temp_level(temp)

    if (
        level != "normal" and level != last_levels["temp"]
        and NOTIFICATION_SETTINGS["temp_alert"]
    ):
        add_notification(
            "오늘", "🌡️", "체온 상승 중",
            f"현재 유아의 체온이 {label} 상태입니다.", "지금"
        )
    last_levels["temp"] = level

    return temp, level, label, history


@app.route("/temperature")
@login_required
def temperature():
    temp, level, label, history = get_current_temperature_state()
    return render_template(
        "temperature.html", temp=temp, level=level, label=label, history=history
    )


@app.route("/temperature/data")
@login_required
def temperature_data():
    global last_llm_run_at
    global latest_llm_result

    temp, level, label, history = get_current_temperature_state()
    now = time.time()

    # 30초마다 실제 센서값으로 LLM 분석
    if now - last_llm_run_at >= LLM_INTERVAL_SECONDS:
        last_llm_run_at = now

        try:
            sensor = fetch_latest_sensor_data()

            if sensor is not None:
                sensor_values = prepare_sensor_values(sensor)

                latest_llm_result = fetch_aws_risk(
                    sensor_values
                )

                print(
                    "실제 센서 기반 LLM 분석 완료:",
                    latest_llm_result
                )
            else:
                print("센서 데이터가 없어 LLM 분석을 건너뜁니다.")

        except requests.exceptions.RequestException as error:
            print("LLM 자동 분석 실패:", error)

    risk_level, risk_text, _ = get_current_risk_state()

    return {
        "temp": temp,
        "level": level,
        "label": label,
        "history": history,
        "risk_level": risk_level,
        "risk_text": risk_text,
        "analysis": get_llm_analysis()
    }


@app.route("/sleep-pattern")
@login_required
def sleep_pattern():
    risk_level, risk_text, _ = get_current_risk_state()

    return render_template(
        "sleep_pattern.html",
        breath=camera.get("breath"),
        sleep_state=camera.get("sleep_state"),
        posture=camera.get("posture"),
        face_state=describe_face_state(camera),
        movement=camera.get("movement"),
        risk_level=risk_level,
        risk_text=risk_text,
        analysis=get_llm_analysis(),
        slots=SLEEP_SLOTS,
        history=SLEEP_HISTORY
    )


@app.route("/live-video")
@login_required
def live_video():
    return render_template("live_video.html", slots=SLEEP_SLOTS)


@app.route("/sleep-record")
@login_required
def sleep_record():
    year = request.args.get("year", type=int) or date.today().year
    month = request.args.get("month", type=int) or date.today().month

    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)

    return render_template(
        "sleep_record.html",
        year=year, month=month, weeks=weeks, today=date.today(),
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month
    )


WEEKDAY_LABELS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


@app.route("/sleep-record/<date_str>")
@login_required
def sleep_record_detail(date_str):
    try:
        selected = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return redirect(url_for("sleep_record"))

    strip_dates = []
    for offset in range(-2, 4):
        d = selected + timedelta(days=offset)
        strip_dates.append({
            "iso": d.isoformat(),
            "day": d.day,
            "weekday": WEEKDAY_LABELS[d.weekday()],
            "selected": d == selected
        })

    return render_template(
        "sleep_record_detail.html",
        selected=selected,
        strip_dates=strip_dates,
        prev_selected=(selected - timedelta(days=6)).isoformat(),
        next_selected=(selected + timedelta(days=6)).isoformat(),
        slots=SLEEP_SLOTS,
        history=SLEEP_HISTORY
    )


@app.route("/first-aid")
@login_required
def first_aid():
    return render_template("first_aid.html", items=FIRST_AID_ITEMS)


@app.route("/first-aid/<slug>")
@login_required
def first_aid_detail(slug):
    data = FIRST_AID_DATA.get(slug)
    if not data:
        abort(404)
    return render_template("first_aid_detail.html", slug=slug, **data)


@app.route("/settings")
@login_required
def settings():
    return render_template("settings.html", user=get_current_user())


@app.route("/settings/profile", methods=["GET", "POST"])
@login_required
def settings_profile():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        birthdate = request.form.get("birthdate", "")

        user = get_current_user()
        photo_path = user["photo"]
        photo_file = request.files.get("photo")
        if photo_file and photo_file.filename:
            filename = secure_filename(f"user_{session['user_email']}_{photo_file.filename}")
            photo_file.save(os.path.join(UPLOAD_FOLDER, filename))
            photo_path = f"uploads/{filename}"

        db = get_db()
        db.execute(
            "UPDATE users SET name = ?, phone = ?, email = ?, birthdate = ?, photo = ? WHERE email = ?",
            (name, phone, email, birthdate, photo_path, session["user_email"])
        )
        db.commit()
        db.close()

        session["user_email"] = email
        session["user_name"] = name
        flash("프로필이 변경되었습니다")
        return redirect(url_for("settings"))

    return render_template("settings_profile.html", user=get_current_user())


@app.route("/settings/general")
@login_required
def settings_general():
    return render_template("settings_general.html")


@app.route("/settings/notifications", methods=["GET", "POST"])
@login_required
def settings_notifications():
    if request.method == "POST":
        for key in NOTIFICATION_SETTINGS:
            NOTIFICATION_SETTINGS[key] = key in request.form
        flash("알림 설정이 저장되었습니다")
        return redirect(url_for("settings_notifications"))

    return render_template("settings_notifications.html", settings=NOTIFICATION_SETTINGS)


@app.route("/settings/password", methods=["GET", "POST"])
@login_required
def settings_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        user = get_current_user()
        if not check_password_hash(user["password_hash"], current):
            flash("현재 비밀번호가 올바르지 않습니다")
            return render_template("settings_password.html")

        if not new or new != confirm:
            flash("새 비밀번호가 일치하지 않습니다")
            return render_template("settings_password.html")

        db = get_db()
        db.execute(
            "UPDATE users SET password_hash = ? WHERE email = ?",
            (generate_password_hash(new), session["user_email"])
        )
        db.commit()
        db.close()

        flash("비밀번호가 변경되었습니다")
        return redirect(url_for("settings_general"))

    return render_template("settings_password.html")


@app.route("/settings/withdraw", methods=["GET", "POST"])
@login_required
def settings_withdraw():
    if request.method == "POST":
        current = request.form.get("current_password", "")

        user = get_current_user()
        if not check_password_hash(user["password_hash"], current):
            flash("현재 비밀번호가 올바르지 않습니다")
            return render_template("settings_withdraw.html")

        db = get_db()
        db.execute("DELETE FROM users WHERE email = ?", (session["user_email"],))
        db.commit()
        db.close()

        session.clear()
        flash("회원 탈퇴가 완료되었습니다")
        return redirect(url_for("splash"))

    return render_template("settings_withdraw.html")


# 노트북의 camera_relay.py가 ESP32-CAM 사진을 받아 올려 주는 최신 프레임
CAMERA_UPLOAD_TOKEN = os.environ.get("CAMERA_UPLOAD_TOKEN")
CAMERA_FRAME_MAX_AGE = 10
latest_frame = {"data": None, "at": 0}


@app.route("/camera/frame", methods=["POST"])
def upload_camera_frame():
    if not CAMERA_UPLOAD_TOKEN:
        abort(503)
    if request.headers.get("X-Camera-Token") != CAMERA_UPLOAD_TOKEN:
        abort(403)

    latest_frame["data"] = request.get_data()
    latest_frame["at"] = time.time()
    return "", 204


@app.route("/camera/frame", methods=["GET"])
@login_required
def camera_frame():
    if not latest_frame["data"] or time.time() - latest_frame["at"] > CAMERA_FRAME_MAX_AGE:
        abort(404)

    return Response(
        latest_frame["data"],
        mimetype="image/jpeg",
        headers={"Cache-Control": "no-store"}
    )


@app.route("/update_camera", methods=["POST"])
def update_camera():
    camera.update(request.json)

    global last_llm_run_at
    global latest_llm_result

    camera.update(request.json)

    now = time.time()
    llm_due = now - last_llm_run_at >= LLM_INTERVAL_SECONDS

    # 마지막 분석으로부터 1분이 지났을 때 LLM 실행
    if llm_due:
        last_llm_run_at = now

        try:
            current_temp = camera.get("temperature", 36.5)
            latest_llm_result = fetch_aws_risk({"object_temp": current_temp})
            print("LLM 자동 분석 완료:", latest_llm_result)

        except requests.exceptions.RequestException as error:
            print("LLM 자동 분석 실패:", error)

    if "temperature" in request.json:
        TEMP_HISTORY.append(camera["temperature"])
        temp_level, temp_label = get_temp_level(camera["temperature"])
        if (
            temp_level != "normal" and temp_level != last_levels["temp"]
            and NOTIFICATION_SETTINGS["temp_alert"]
        ):
            add_notification(
                "오늘", "🌡️", "체온 상승 중",
                f"현재 유아의 체온이 {temp_label} 상태입니다.", "지금"
            )
        last_levels["temp"] = temp_level

    risk_level, risk_text = get_risk(camera)
    if (
        risk_level != "normal" and risk_level != last_levels["risk"]
        and NOTIFICATION_SETTINGS["sleep_alert"]
    ):
        add_notification(
            "오늘", "🛌", "위험행동 감지",
            f"현재 유아의 상태가 {risk_text} 단계입니다.", "지금"
        )
    last_levels["risk"] = risk_level

    return "OK"


init_db()

if __name__ == "__main__":
    app.run(debug=True)
