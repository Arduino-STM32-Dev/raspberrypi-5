// ============================================================
// STM32F103 + DHT11 + 风扇 + 舵机阀门 + OLED
//   · 新增：接收 FAN_PWM:<0-255> 指令，实现 PID 平滑调速
//   · 上报：T:温度,H:湿度,F:风扇(0/1),V:阀门(0/1),P:PWM(0-255)
//   · 风扇仅由指令控制（配合树莓派 PID）；阀门保留高温自动开（可关）
// 串口：Serial(PA9/PA10) @9600，接 ESP32 的 GPIO17/GPIO16
// ============================================================
#include <DHT.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <Servo.h>

// --- 引脚定义 ---
#define DHTPIN PA0
#define DHTTYPE DHT11
#define AIN1 PB12
#define AIN2 PB13
#define PWMA PA5
#define BUTTON_PIN PA2
#define SERVO_PIN PB0

// 高温自动开阀门（风扇交给 PID 控制，这里只保留阀门自动逻辑；不需要就改成 0）
#define AUTO_VALVE_ON_HOT 1
#define HOT_TEMP 28.0

DHT dht(DHTPIN, DHTTYPE);
Servo valveServo;
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);

// --- 状态变量 ---
bool isFanOn = false;
bool isValveOpen = false;
int currentPwm = 0;              // 当前风扇 PWM(0-255)
int lastButtonState = HIGH;

unsigned long lastDhtReadTime = 0;
unsigned long lastSerialSendTime = 0;
unsigned long lastOledUpdateTime = 0;
const long dhtInterval = 2000;
const long serialInterval = 2000;
const long oledInterval = 500;
float currentTemp = 0.0;
float currentHumi = 0.0;

void setFanPwm(int pwm) {
  if (pwm < 0) pwm = 0;
  if (pwm > 255) pwm = 255;
  currentPwm = pwm;
  isFanOn = (pwm > 0);
  analogWrite(PWMA, pwm);
}

void setup() {
  pinMode(AIN1, OUTPUT);
  pinMode(AIN2, OUTPUT);
  pinMode(PWMA, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  digitalWrite(AIN1, HIGH);
  digitalWrite(AIN2, LOW);
  setFanPwm(0);

  Serial.begin(9600);
  Serial.setTimeout(10);
  dht.begin();

  u8g2.begin();
  u8g2.setFont(u8g2_font_ncenB08_tr);

  valveServo.attach(SERVO_PIN);
  valveServo.write(0);
  isValveOpen = false;

  Serial.println("Industrial System Booted (PID-ready)...");
}

void loop() {
  unsigned long currentMillis = millis();

  // 任务1：本地按键（切换风扇全速/停）
  int currentButtonState = digitalRead(BUTTON_PIN);
  if (currentButtonState == LOW && lastButtonState == HIGH) {
    setFanPwm(isFanOn ? 0 : 150);
    delay(50);
  }
  lastButtonState = currentButtonState;

  // 任务2：读 DHT11 + 阀门高温自动
  if (currentMillis - lastDhtReadTime >= dhtInterval) {
    lastDhtReadTime = currentMillis;
    float t = dht.readTemperature();
    float h = dht.readHumidity();
    if (!isnan(t) && !isnan(h)) {
      currentTemp = t;
      currentHumi = h;
#if AUTO_VALVE_ON_HOT
      if (currentTemp > HOT_TEMP) {
        if (!isValveOpen) { isValveOpen = true; valveServo.write(90); }
      } else {
        if (isValveOpen) { isValveOpen = false; valveServo.write(0); }
      }
#endif
    }
  }

  // 任务3：接收远程指令（风扇 PWM / 开关 / 阀门）
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "FAN_ON")  { setFanPwm(150); }
    if (cmd == "FAN_OFF") { setFanPwm(0); }
    if (cmd.startsWith("FAN_PWM:")) {          // PID 动态调速
      setFanPwm(cmd.substring(8).toInt());
    }
    if (cmd == "VALVE_OPEN")  { isValveOpen = true; valveServo.write(90); }
    if (cmd == "VALVE_CLOSE") { isValveOpen = false; valveServo.write(0); }
  }

  // 任务4：OLED（500ms）
  if (currentMillis - lastOledUpdateTime >= oledInterval) {
    lastOledUpdateTime = currentMillis;
    u8g2.clearBuffer();
    u8g2.setCursor(0, 15); u8g2.print("Temp: "); u8g2.print(currentTemp); u8g2.print(" C");
    u8g2.setCursor(0, 30); u8g2.print("Humi: "); u8g2.print(currentHumi); u8g2.print(" %");
    u8g2.setCursor(0, 45); u8g2.print("Fan PWM: "); u8g2.print(currentPwm);
    u8g2.setCursor(0, 60); u8g2.print("Valve: "); u8g2.print(isValveOpen ? "OPEN" : "CLOSED");
    u8g2.sendBuffer();
  }

  // 任务5：串口上报（含 PWM P:）
  if (currentMillis - lastSerialSendTime >= serialInterval) {
    lastSerialSendTime = currentMillis;
    Serial.print("T:"); Serial.print(currentTemp);
    Serial.print(",H:"); Serial.print(currentHumi);
    Serial.print(",F:"); Serial.print(isFanOn ? 1 : 0);
    Serial.print(",V:"); Serial.print(isValveOpen ? 1 : 0);
    Serial.print(",P:"); Serial.println(currentPwm);
  }
}
