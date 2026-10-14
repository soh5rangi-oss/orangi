#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>   // 라이브러리 매니저에서 "ArduinoJson" (Benoit Blanchon) 설치
#include <Wire.h>
#include <Adafruit_MLX90614.h>  // 라이브러리 매니저에서 "Adafruit MLX90614 Library" 설치

// ---- 설정 ----
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

const char* SERVER_BASE = "https://baby-monitor-backend.onrender.com";

// 앱에 로그인할 때 쓰는 계정과 동일하게 입력 (체온 데이터가 이 계정에 쌓임)
// 주의: 비밀번호에 '&', '=', '+' 문자가 있으면 이 간단한 구현에서 깨질 수 있음
const char* USER_EMAIL = "YOUR_LOGIN_EMAIL";
const char* USER_PASSWORD = "YOUR_LOGIN_PASSWORD";

const char* DEVICE_ID = "mlx90614-1";

const unsigned long SEND_INTERVAL_MS = 1000;        // 측정 전송 주기
const unsigned long TOKEN_LIFETIME_MS = 23UL * 60 * 60 * 1000; // 토큰 23시간마다 갱신 (서버 만료 24시간)

// ---- 전역 상태 ----
Adafruit_MLX90614 mlx = Adafruit_MLX90614();
String accessToken = "";
unsigned long tokenIssuedAt = 0;

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi 연결 중");
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("연결됨, IP: ");
  Serial.println(WiFi.localIP());
}

bool login() {
  WiFiClientSecure client;
  client.setInsecure(); // 인증서 검증 생략 (간단한 개인 프로젝트용)

  HTTPClient http;
  http.begin(client, String(SERVER_BASE) + "/api/auth/login");
  http.addHeader("Content-Type", "application/x-www-form-urlencoded");

  String body = "username=" + String(USER_EMAIL) + "&password=" + String(USER_PASSWORD);
  int status = http.POST(body);

  bool ok = false;
  if (status == 200) {
    String resp = http.getString();
    JsonDocument doc;
    if (deserializeJson(doc, resp) == DeserializationError::Ok) {
      accessToken = doc["access_token"].as<String>();
      tokenIssuedAt = millis();
      ok = accessToken.length() > 0;
    }
  } else {
    Serial.printf("로그인 실패: HTTP %d\n", status);
  }

  http.end();
  return ok;
}

bool sendTemperature(float tempC) {
  WiFiClientSecure client;
  client.setInsecure();

  HTTPClient http;
  http.begin(client, String(SERVER_BASE) + "/api/sensor/temperature");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", "Bearer " + accessToken);

  String payload = "{\"device_id\":\"" + String(DEVICE_ID) +
                    "\",\"temperature\":" + String(tempC, 2) + "}";
  int status = http.POST(payload);

  if (status == 401) {
    Serial.println("토큰 만료, 재로그인 시도");
    http.end();
    return login() && sendTemperature(tempC);
  }

  if (status != 200) {
    Serial.printf("전송 실패: HTTP %d\n", status);
  }

  http.end();
  return status == 200;
}

void setup() {
  Serial.begin(115200);
  Wire.begin();

  if (!mlx.begin()) {
    Serial.println("MLX90614 센서를 찾을 수 없습니다. 배선을 확인하세요.");
  }

  connectWifi();

  while (!login()) {
    Serial.println("로그인 재시도...");
    delay(3000);
  }
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }

  if (millis() - tokenIssuedAt > TOKEN_LIFETIME_MS) {
    login();
  }

  float tempC = mlx.readObjectTempC();  // 비접촉 측정 대상(이마 등)의 온도
  Serial.printf("측정 온도: %.2f C\n", tempC);

  sendTemperature(tempC);

  delay(SEND_INTERVAL_MS);
}
