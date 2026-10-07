import calendar
import os
import sqlite3
from collections import deque
from datetime import date, datetime, timedelta
from functools import wraps

import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
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

def fetch_aws_risk(temp):
    breath_status = camera.get("breath", "정상")

    breathing_rate = camera.get("breathing_rate")
    if breathing_rate is None:
        breathing_rate = 30 if breath_status == "정상" else 18

    movement_text = camera.get("movement", "정상")
    if movement_text in ["거의 없음", "없음"]:
        movement_value = "very low"
    elif movement_text in ["적음", "낮음"]:
        movement_value = "low"
    else:
        movement_value = "normal"

    face_text = describe_face_state(camera)

    situation_text = (
        f"호흡 상태: {breath_status}, "
        f"자세: {camera.get('posture', '정보 없음')}, "
        f"얼굴 상태: {face_text}, "
        f"수면 상태: {camera.get('sleep_state', '정보 없음')}"
    )

    payload = {
        "temperature": temp,
        "breathing_rate": breathing_rate,
        "movement": movement_value,
        "situation_text": situation_text
    }

    response = requests.post(
        AWS_RISK_API_URL,
        json=payload,
        timeout=15
    )
    response.raise_for_status()

    return response.json().get("risk_result")

def get_risk(camera_state):
    if camera_state.get("blanket"):
        return "danger", "위험"
    if camera_state.get("movement") == "많음":
        return "caution", "주의"
    return "normal", "정상"


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

    risk_level, risk_text = get_risk(camera)
    llm = "AI 분석 결과를 불러오는 중입니다."

    try:
        aws_result = fetch_aws_risk(temp)

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

    except requests.exceptions.RequestException:
        llm = "AWS 위험 분석 서버에 연결할 수 없습니다."

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


def fetch_backend_temperature_chart():
    token = session.get("access_token")
    if not token:
        return None
    try:
        resp = requests.get(
            f"{BACKEND_URL}/api/temperature/chart",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
    except requests.exceptions.RequestException:
        return None
    return resp.json() if resp.ok else None


def get_current_temperature_state():
    chart = fetch_backend_temperature_chart()

    if chart and chart.get("temperatures"):
        labels = chart["labels"][-6:]
        temps = chart["temperatures"][-6:]
        temp = temps[-1]
        history = [
            {"label": lbl, "value": val, "percent": temp_percent(val)}
            for lbl, val in zip(labels, temps)
        ]
    else:
        temp = camera.get("temperature", 36.5)
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
    temp, level, label, history = get_current_temperature_state()
    return {"temp": temp, "level": level, "label": label, "history": history}


@app.route("/sleep-pattern")
@login_required
def sleep_pattern():
    risk_level, risk_text = get_risk(camera)

    return render_template(
        "sleep_pattern.html",
        breath=camera.get("breath"),
        sleep_state=camera.get("sleep_state"),
        posture=camera.get("posture"),
        face_state=describe_face_state(camera),
        movement=camera.get("movement"),
        risk_level=risk_level,
        risk_text=risk_text,
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


@app.route("/update_camera", methods=["POST"])
def update_camera():
    camera.update(request.json)

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
