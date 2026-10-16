# PID 温度控制 + PWM 网页监控

> 在树莓派端引入 PID，动态下发 PWM 给 STM32 风扇，实现平滑温控；网页看板新增 PWM 实时曲线。
> 链路：**STM32(测温) → ESP32(MQTT 网关) → 树莓派(PID) → MQTT → ESP32 → STM32(风扇调速)**。

## 一、原理
- 树莓派订阅 home/fan/status 读取温度 T:，用位置式 PID 计算 PWM(0~255)，每 2 秒下发 FAN_PWM:<值> 到 pi/to/esp32。
- 温度高于目标 → PWM 增大（风扇更猛）降温；低于目标 → 归零。
- PID 带输出限幅、抗积分饱和、误差死区（防临界抖动）。
- STM32 收到 FAN_PWM 后 analogWrite 平滑调速，并上报实际 PWM（P:）供网页/看板绘制曲线。

## 二、树莓派 PID 控制器 pid_fan_control.py
```python
#!/usr/bin/env python3
"""树莓派端 PID 温控：读 STM32 温度，用 PID 计算 PWM，下发 FAN_PWM:<0-255>，实现平滑控温。

链路：STM32(测温) -> ESP32 -> MQTT(home/fan/status) -> [本脚本 PID] -> MQTT(pi/to/esp32) -> ESP32 -> STM32
- 温度高于目标 -> 输出更大 PWM（风扇更猛）实现降温
- 输出限幅 0~255，带抗积分饱和
- 参数用环境变量可调（见下）
"""

import os
import re
import time

import paho.mqtt.client as mqtt

# ================= 配置（可用环境变量覆盖）=================
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
SUB_TOPIC = os.environ.get("SUB_TOPIC", "home/fan/status")
PUB_TOPIC = os.environ.get("PUB_TOPIC", "pi/to/esp32")

SETPOINT = float(os.environ.get("PID_SETPOINT", "28.0"))   # 目标温度(°C)
KP = float(os.environ.get("PID_KP", "20.0"))               # 比例
KI = float(os.environ.get("PID_KI", "1.0"))                # 积分
KD = float(os.environ.get("PID_KD", "6.0"))                # 微分
INTERVAL = float(os.environ.get("PID_INTERVAL", "2.0"))    # 控制周期(秒)
PWM_MIN = int(os.environ.get("PID_PWM_MIN", "0"))
PWM_MAX = int(os.environ.get("PID_PWM_MAX", "255"))
# 误差死区：|err| 小于它则维持上次输出，避免临界抖动
DEADBAND = float(os.environ.get("PID_DEADBAND", "0.2"))
# ==========================================================

_latest_temp = None
_last_ts = 0.0


def parse_temp(payload):
    m = re.search(r"T\s*[:=]\s*(-?\d+(?:\.\d+)?)", payload)
    return float(m.group(1)) if m else None


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(SUB_TOPIC)
        print("已订阅 " + SUB_TOPIC)
    else:
        print("MQTT 连接失败 rc=" + str(rc))


def on_message(client, userdata, msg):
    global _latest_temp, _last_ts
    t = parse_temp(msg.payload.decode("utf-8", errors="replace"))
    if t is not None:
        _latest_temp = t
        _last_ts = time.time()


class PID:
    """位置式 PID：输出 = Kp*e + Ki*∫e + Kd*de/dt，带限幅与抗积分饱和。"""

    def __init__(self, kp, ki, kd, out_min, out_max):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.out_min = out_min
        self.out_max = out_max
        self.integral = 0.0
        self.prev_meas = None
        self.prev_time = None

    def compute(self, setpoint, meas, now):
        error = meas - setpoint                  # 高于目标 => 正误差 => 加大风量
        dt = INTERVAL if self.prev_time is None else max(1e-3, now - self.prev_time)
        self.integral += error * dt
        deriv = 0.0 if self.prev_meas is None else (meas - self.prev_meas) / dt

        out = self.kp * error + self.ki * self.integral + self.kd * deriv

        # 限幅 + 回算抗积分饱和
        if out > self.out_max:
            out = self.out_max
            self.integral -= error * dt
        elif out < self.out_min:
            out = self.out_min
            self.integral -= error * dt

        self.prev_meas = meas
        self.prev_time = now
        return out

    def reset(self):
        self.integral = 0.0
        self.prev_meas = None
        self.prev_time = None


def make_client():
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, "pi_pid")
    except (AttributeError, TypeError):
        return mqtt.Client("pi_pid")


def main():
    client = make_client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()

    pid = PID(KP, KI, KD, PWM_MIN, PWM_MAX)
    last_pwm = 0
    print("PID 温控启动: 目标=" + str(SETPOINT) + "C  Kp=" + str(KP) + " Ki=" + str(KI)
          + " Kd=" + str(KD) + " 周期=" + str(INTERVAL) + "s")

    while True:
        time.sleep(INTERVAL)
        t = _latest_temp
        if t is None:
            print("暂无温度数据…")
            continue

        error = t - SETPOINT
        if abs(error) < DEADBAND:
            pwm = last_pwm                      # 死区内维持
        else:
            pwm = int(round(pid.compute(SETPOINT, t, time.time())))
            pwm = max(PWM_MIN, min(PWM_MAX, pwm))
            last_pwm = pwm

        client.publish(PUB_TOPIC, "FAN_PWM:" + str(pwm))
        print("T=" + format(t, ".1f") + "C 目标=" + format(SETPOINT, ".1f")
              + " 误差=" + format(error, "+.1f") + " -> PWM=" + str(pwm))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("PID 温控已停止。")
```

## 三、STM32 固件（新增 FAN_PWM 指令 + P: 上报）
```cpp
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
```

## 四、网页看板后端 app.py
```python
#!/usr/bin/env python3
"""物联网监控看板后端（含舵机阀门控制 + 实时温度曲线）。

- Flask 提供网页 + REST API
- paho-mqtt 订阅 home/fan/status 接收 ESP32 转发的 STM32 数据
  格式：T:25.0,H:60.0,F:1,V:0   （F=风扇 1开；V=阀门/舵机 1开）
- 数据存入 SQLite，前端 ECharts 画实时温度曲线
- 下发指令到 pi/to/esp32：FAN_ON / FAN_OFF / VALVE_OPEN / VALVE_CLOSE
"""

import json
import os
import re
import sqlite3
import threading
import time

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, render_template, request

# ================= 配置 =================
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
SUB_TOPIC = os.environ.get("SUB_TOPIC", "home/fan/status")
PUB_TOPIC = os.environ.get("PUB_TOPIC", "pi/to/esp32")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensor.db")
HOST = "0.0.0.0"
PORT = 5000

# device+action -> STM32 固件识别的纯文本指令
CMD_MAP = {
    ("fan", "on"): "FAN_ON",
    ("fan", "off"): "FAN_OFF",
    ("valve", "open"): "VALVE_OPEN",
    ("valve", "close"): "VALVE_CLOSE",
}

# ================= SQLite =================
_lock = threading.Lock()
_latest = {"temperature": None, "humidity": None, "fan": None, "valve": None, "pwm": None, "ts": None}


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS readings ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts REAL NOT NULL,"
        " temperature REAL,"
        " humidity REAL,"
        " fan INTEGER,"
        " valve INTEGER,"
        " pwm INTEGER)"
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(readings)").fetchall()]
    if "valve" not in cols:
        conn.execute("ALTER TABLE readings ADD COLUMN valve INTEGER")
    if "pwm" not in cols:
        conn.execute("ALTER TABLE readings ADD COLUMN pwm INTEGER")
    conn.commit()
    return conn


def insert_reading(temp, hum, fan, valve, pwm):
    with _lock:
        conn = _db()
        conn.execute(
            "INSERT INTO readings (ts, temperature, humidity, fan, valve, pwm) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), temp, hum, fan, valve, pwm),
        )
        conn.commit()
        conn.close()


def query_history(limit=500):
    with _lock:
        conn = _db()
        rows = conn.execute(
            "SELECT ts, temperature, humidity, fan, valve, pwm FROM readings ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
    rows.reverse()
    return rows


# ================= MQTT =================
mqtt_connected = False
mqtt_client = mqtt.Client(client_id="pi_iot_dashboard")


def on_connect(client, userdata, flags, rc):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        client.subscribe(SUB_TOPIC)
        print("MQTT 已连接，订阅 " + SUB_TOPIC)
    else:
        mqtt_connected = False
        print("MQTT 连接失败 rc=" + str(rc))


def on_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False
    print("MQTT 已断开 rc=" + str(rc))


def _kind(name):
    n = str(name).lower()
    if n in ("temperature", "temp", "t", "wendu", "温度"):
        return "t"
    if n in ("humidity", "humi", "hum", "h", "shidu", "湿度"):
        return "h"
    if n in ("f", "fan", "fengshan", "风扇"):
        return "f"
    if n in ("v", "valve", "famen", "阀", "阀门"):
        return "v"
    return None


def parse_sensor(payload):
    """兼容多种报文，返回 (温度, 湿度, 风扇, 阀门)。"""
    temp = hum = fan = valve = None

    if payload.startswith("{"):
        try:
            for k, v in json.loads(payload).items():
                kind = _kind(k)
                if kind == "t":
                    temp = float(v)
                elif kind == "h":
                    hum = float(v)
                elif kind == "f":
                    fan = int(float(v))
                elif kind == "v":
                    valve = int(float(v))
            if any(x is not None for x in (temp, hum, fan, valve)):
                return temp, hum, fan, valve
        except Exception:  # noqa: BLE001
            pass

    for k, v in re.findall(r"([A-Za-z\u4e00-\u9fff]+)\s*[:=]\s*(-?\d+(?:\.\d+)?)", payload):
        kind = _kind(k)
        if kind == "t":
            temp = float(v)
        elif kind == "h":
            hum = float(v)
        elif kind == "f":
            fan = int(float(v))
        elif kind == "v":
            valve = int(float(v))
    if any(x is not None for x in (temp, hum, fan, valve)):
        return temp, hum, fan, valve

    parts = payload.split(":")
    if len(parts) == 3 and parts[0].strip().upper() == "SENSOR":
        try:
            return float(parts[1]), float(parts[2]), None, None
        except ValueError:
            pass

    nums = re.findall(r"-?\d+(?:\.\d+)?", payload)
    if len(nums) == 2:
        return float(nums[0]), float(nums[1]), None, None

    return None, None, None, None


def parse_pwm(payload):
    m = re.search(r"[Pp]\s*[:=]\s*(\d+)", payload)
    return int(m.group(1)) if m else None


def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8", errors="replace").strip()
    print("IN " + msg.topic + ": " + payload)
    temp, hum, fan, valve = parse_sensor(payload)
    pwm = parse_pwm(payload)
    if all(x is None for x in (temp, hum, fan, valve)) and pwm is None:
        print("   未识别")
        return
    if temp is not None:
        _latest["temperature"] = temp
    if hum is not None:
        _latest["humidity"] = hum
    if fan is not None:
        _latest["fan"] = fan
    if valve is not None:
        _latest["valve"] = valve
    if pwm is not None:
        _latest["pwm"] = pwm
    _latest["ts"] = time.time()
    insert_reading(temp, hum, fan, valve, pwm)
    print("   温度=" + str(temp) + " 湿度=" + str(hum) + " 风扇=" + str(fan) + " 阀门=" + str(valve) + " PWM=" + str(pwm))


mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message


def mqtt_loop():
    while True:
        try:
            mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            mqtt_client.loop_forever()
        except Exception as e:  # noqa: BLE001
            print("MQTT 异常，5 秒后重试: " + str(e))
            time.sleep(5)


# ================= Flask =================
app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/latest")
def api_latest():
    return jsonify(_latest)


@app.route("/api/history")
def api_history():
    limit = request.args.get("limit", default=500, type=int)
    limit = max(1, min(limit, 5000))
    rows = query_history(limit)
    return jsonify([
        {"ts": r[0] * 1000, "temperature": r[1], "humidity": r[2], "fan": r[3], "valve": r[4], "pwm": r[5]}
        for r in rows
    ])


@app.route("/api/command", methods=["POST"])
def api_command():
    body = request.get_json(silent=True) or {}
    cmd = body.get("command") or CMD_MAP.get((body.get("device"), body.get("action")))
    if not cmd:
        return jsonify({"ok": False, "error": "无法识别的指令"}), 400
    mqtt_client.publish(PUB_TOPIC, cmd)
    print("OUT " + PUB_TOPIC + ": " + cmd)
    return jsonify({"ok": True, "command": cmd})


@app.route("/api/status")
def api_status():
    return jsonify({
        "mqtt_connected": mqtt_connected,
        "broker": MQTT_BROKER + ":" + str(MQTT_PORT),
        "sub_topic": SUB_TOPIC,
        "pub_topic": PUB_TOPIC,
    })


if __name__ == "__main__":
    threading.Thread(target=mqtt_loop, daemon=True).start()
    print("看板已启动: http://<树莓派IP>:" + str(PORT))
    app.run(host=HOST, port=PORT, debug=False)
```

## 五、网页看板前端 index.html
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Thea 监控看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #0f172a; color: #e2e8f0; min-height: 100vh; padding: 16px; }
  header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
  h1 { font-size: 20px; font-weight: 600; }
  .status { font-size: 13px; padding: 6px 12px; border-radius: 999px; background: #1e293b; }
  .status.on { color: #4ade80; } .status.off { color: #f87171; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .status.on .dot { background: #4ade80; } .status.off .dot { background: #f87171; }
  .cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-bottom: 14px; }
  .card { background: #1e293b; border-radius: 14px; padding: 14px 8px; text-align: center; }
  .card .label { font-size: 12px; color: #94a3b8; margin-bottom: 6px; }
  .card .value { font-size: 26px; font-weight: 700; }
  .card .unit { font-size: 13px; color: #94a3b8; }
  .temp .value { color: #fb923c; } .humi .value { color: #38bdf8; }
  .fan .value { color: #34d399; } .valve .value { color: #a78bfa; } .pwm .value { color: #f472b6; }
  .chart-box { background: #1e293b; border-radius: 14px; padding: 10px; margin-bottom: 14px; }
  .chart-title { font-size: 14px; color: #cbd5e1; margin: 4px 0 6px 6px; }
  #chart { width: 100%; height: 340px; }
  .controls { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
  .btn { border: none; border-radius: 12px; padding: 15px 0; font-size: 15px; font-weight: 600;
         color: #0f172a; cursor: pointer; transition: transform .05s, opacity .15s; }
  .btn:active { transform: scale(.96); }
  .fan-on { background: #34d399; } .fan-off { background: #64748b; color: #fff; }
  .valve-open { background: #a78bfa; } .valve-close { background: #64748b; color: #fff; }
  .toast { position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%);
           background: #334155; color: #fff; padding: 10px 20px; border-radius: 10px;
           font-size: 14px; opacity: 0; transition: opacity .3s; pointer-events: none; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
  <header>
    <h1>🏭 工业监控看板</h1>
    <div class="status off" id="status"><span class="dot"></span>连接中…</div>
  </header>

  <div class="cards">
    <div class="card temp"><div class="label">🌡️ 温度</div><div class="value"><span id="temp">--</span><span class="unit"> °C</span></div></div>
    <div class="card humi"><div class="label">💧 湿度</div><div class="value"><span id="humi">--</span><span class="unit"> %</span></div></div>
    <div class="card fan"><div class="label">🌀 风扇</div><div class="value"><span id="fan">--</span></div></div>
    <div class="card valve"><div class="label">🔧 阀门</div><div class="value"><span id="valve">--</span></div></div>
    <div class="card pwm"><div class="label">⚙️ PWM</div><div class="value"><span id="pwm">--</span></div></div>
  </div>

  <div class="chart-box">
    <div class="chart-title">📈 温度实时曲线</div>
    <div id="chart"></div>
  </div>

  <div class="chart-box">
    <div class="chart-title">⚙️ 风扇 PWM 曲线（PID 输出）</div>
    <div id="pwmchart"></div>
  </div>

  <div class="controls">
    <button class="btn fan-on" id="fan-on">🌀 开风扇</button>
    <button class="btn fan-off" id="fan-off">⏹️ 关风扇</button>
    <button class="btn valve-open" id="valve-open">🔓 开阀门</button>
    <button class="btn valve-close" id="valve-close">🔒 关阀门</button>
  </div>

  <div class="toast" id="toast"></div>

<script>
  var chart = echarts.init(document.getElementById('chart'));
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['温度', '湿度'], textStyle: { color: '#94a3b8' } },
    grid: { left: 46, right: 46, top: 40, bottom: 28 },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#94a3b8' }
    },
    yAxis: [
      { type: 'value', name: '温度(°C)', min: 0, max: 50, nameTextStyle: { color: '#fb923c' },
        axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1e293b' } } },
      { type: 'value', name: '湿度(%)', min: 0, max: 100, nameTextStyle: { color: '#38bdf8' },
        axisLabel: { color: '#94a3b8' }, splitLine: { show: false } }
    ],
    series: [
      { name: '温度', type: 'line', showSymbol: false, smooth: true,
        lineStyle: { color: '#fb923c', width: 3 }, areaStyle: { color: 'rgba(251,146,60,0.15)' },
        data: [] },
      { name: '湿度', type: 'line', yAxisIndex: 1, showSymbol: false, smooth: true,
        lineStyle: { color: '#38bdf8', width: 1.5 }, data: [] }
    ]
  });

  var pwmchart = echarts.init(document.getElementById('pwmchart'));
  pwmchart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { left: 46, right: 20, top: 20, bottom: 28 },
    xAxis: { type: 'time', axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8' } },
    yAxis: { type: 'value', name: 'PWM', min: 0, max: 255, nameTextStyle: { color: '#f472b6' }, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#1e293b' } } },
    series: [ { name: 'PWM', type: 'line', showSymbol: false, smooth: true, step: 'middle', lineStyle: { color: '#f472b6', width: 2 }, areaStyle: { color: 'rgba(244,114,182,0.15)' }, data: [] } ]
  });

  function showToast(msg) {
    var t = document.getElementById('toast');
    t.textContent = msg; t.classList.add('show');
    setTimeout(function () { t.classList.remove('show'); }, 2000);
  }
  function setStatus(ok) {
    var s = document.getElementById('status');
    s.className = 'status ' + (ok ? 'on' : 'off');
    s.innerHTML = '<span class="dot"></span>' + (ok ? 'MQTT 已连接' : 'MQTT 未连接');
  }
  function yn(v) { return v == 1 ? '开' : '关'; }

  function refresh() {
    fetch('/api/history?limit=300').then(function (r) { return r.json(); }).then(function (hist) {
      fetch('/api/latest').then(function (r) { return r.json(); }).then(function (latest) {
        fetch('/api/status').then(function (r) { return r.json(); }).then(function (st) {
          setStatus(st.mqtt_connected);
          if (latest.temperature != null) document.getElementById('temp').textContent = latest.temperature;
          if (latest.humidity != null) document.getElementById('humi').textContent = latest.humidity;
          if (latest.fan != null) document.getElementById('fan').textContent = yn(latest.fan);
          if (latest.valve != null) document.getElementById('valve').textContent = yn(latest.valve);
          if (latest.pwm != null) document.getElementById('pwm').textContent = latest.pwm;
          chart.setOption({
            series: [
              { data: hist.map(function (p) { return [p.ts, p.temperature]; }) },
              { data: hist.map(function (p) { return [p.ts, p.humidity]; }) }
            ]
          });
          pwmchart.setOption({ series: [ { data: hist.map(function (p) { return [p.ts, p.pwm]; }) } ] });
        });
      });
    }).catch(function () { setStatus(false); });
  }

  function sendCmd(device, action, label) {
    fetch('/api/command', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device: device, action: action })
    }).then(function (r) { return r.json(); }).then(function (j) {
      showToast(j.ok ? '✅ ' + label + ' 指令已发送' : '⚠️ 发送失败');
    }).catch(function () { showToast('⚠️ 网络错误'); });
  }

  document.getElementById('fan-on').onclick = function () { sendCmd('fan', 'on', '开风扇'); };
  document.getElementById('fan-off').onclick = function () { sendCmd('fan', 'off', '关风扇'); };
  document.getElementById('valve-open').onclick = function () { sendCmd('valve', 'open', '开阀门'); };
  document.getElementById('valve-close').onclick = function () { sendCmd('valve', 'close', '关阀门'); };

  refresh();
  setInterval(refresh, 2000);
  window.addEventListener('resize', function () { chart.resize(); pwmchart.resize(); });
</script>
</body>
</html>
```

## 六、运行与调参
```bash
python3 ~/deepseek_project/pid_fan_control.py    # 启动 PID（默认目标 28C）
```

参数用环境变量覆盖：
```bash
PID_SETPOINT=28 PID_KP=20 PID_KI=1 PID_KD=6 PID_INTERVAL=2 PID_DEADBAND=0.2 python3 pid_fan_control.py
```

- 先只用 KP，观察超调；再加 KD 抑制振荡；KI 最后加且要小（消稳态误差）。
- 必须先把 STM32 固件烧入（支持 FAN_PWM），否则风扇不会响应。

## 七、指令与上报格式
| 方向 | 主题 | 内容 |
| :--- | :--- | :--- |
| 上行 | home/fan/status | T:温度,H:湿度,F:风扇(0/1),V:阀门(0/1),P:PWM(0-255) |
| 下行 | pi/to/esp32 | FAN_ON / FAN_OFF / FAN_PWM:<0-255> / VALVE_OPEN / VALVE_CLOSE |

## 八、避坑
1. paho-mqtt 2.x 的 VERSION2 回调签名是 5 个参数，旧式 (client,userdata,flags,rc) 会报 TypeError；本脚本用 VERSION1。
2. STM32 原有“温度>28 自动开风扇”的阈值逻辑必须去掉，否则与 PID 抢控制权；本固件风扇仅由指令驱动（阀门自动逻辑保留）。
3. 时间列/LED 等前端问题见仓库其他文档。