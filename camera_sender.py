import cv2
import requests



SERVER = "http://127.0.0.1:5000/update_camera"

cap = cv2.VideoCapture(0)

face = cv2.CascadeClassifier(
    cv2.data.haarcascades +
    "haarcascade_frontalface_default.xml"
)

ret, prev = cap.read()
prev = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)

while True:

    ret, frame = cap.read()

    if not ret:
        continue

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    faces = face.detectMultiScale(gray,1.1,5)

    face_visible = len(faces) > 0

    diff = cv2.absdiff(prev, gray)
    movement = diff.mean()

    if movement > 15:
        movement_state = "많음"
    else:
        movement_state = "정상"

    # 테스트용 (나중에 YOLO로 교체)
    blanket = not face_visible

    requests.post(
        SERVER,
        json={
            "face_visible": face_visible,
            "movement": movement_state,
            "blanket": blanket
        }
    )

    cv2.imshow("Camera", frame)

    prev = gray

    if cv2.waitKey(1) == 27:
        break

cap.release()
cv2.destroyAllWindows()