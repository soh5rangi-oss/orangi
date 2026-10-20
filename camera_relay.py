"""ESP32-CAM 사진을 받아 서버(Render)로 계속 올려 주는 중계 스크립트.

노트북과 ESP32-CAM이 같은 Wi-Fi에 있을 때 노트북에서 실행한다.

    set RELAY_SERVER_URL=https://<Render 사이트 주소>
    set CAMERA_UPLOAD_TOKEN=서버에 설정한 토큰
    python camera_relay.py

환경 변수
    ESP32_CAPTURE_URL   ESP32-CAM 사진 주소 (기본: http://192.168.0.18/capture)
    RELAY_SERVER_URL    사진을 올릴 서버 주소 (필수, 예: https://xxx.onrender.com)
    CAMERA_UPLOAD_TOKEN 서버의 CAMERA_UPLOAD_TOKEN과 같은 값 (필수)
"""

import os
import sys
import time

import requests

ESP32_CAPTURE_URL = os.environ.get("ESP32_CAPTURE_URL", "http://192.168.0.18/capture")
RELAY_SERVER_URL = os.environ.get("RELAY_SERVER_URL", "").rstrip("/")
CAMERA_UPLOAD_TOKEN = os.environ.get("CAMERA_UPLOAD_TOKEN")

RETRY_SECONDS = 3

if not RELAY_SERVER_URL or not CAMERA_UPLOAD_TOKEN:
    sys.exit("RELAY_SERVER_URL과 CAMERA_UPLOAD_TOKEN 환경 변수를 설정하세요.")

RELAY_SERVER_URL += "/camera/frame"

session = requests.Session()
print(f"{ESP32_CAPTURE_URL} -> {RELAY_SERVER_URL} 중계 시작 (Ctrl+C로 종료)")

sent = 0
started = time.time()

while True:
    try:
        frame = session.get(ESP32_CAPTURE_URL, timeout=5)
        frame.raise_for_status()

        res = session.post(
            RELAY_SERVER_URL,
            data=frame.content,
            headers={
                "Content-Type": "image/jpeg",
                "X-Camera-Token": CAMERA_UPLOAD_TOKEN
            },
            timeout=10
        )
        res.raise_for_status()

        sent += 1
        if sent % 20 == 0:
            fps = sent / (time.time() - started)
            print(f"{sent}장 전송 (평균 {fps:.1f}장/초)")

    except requests.exceptions.RequestException as error:
        print("중계 실패:", error)
        time.sleep(RETRY_SECONDS)
