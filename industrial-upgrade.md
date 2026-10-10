# 工业监控升级：STM32 OLED + 舵机阀门 + Grafana 工业看板

> 本文汇总本次升级的**全部代码与步骤**。
> 链路：**STM32(DHT11 + 风扇 + 舵机阀门 + OLED) → ESP32(MQTT 网关) → 树莓派(Flask 看板 / 语音助手 / Grafana)**。

## 一、本次改动一览
- **STM32**：新增 SSD1306 OLED 实时显示；新增舵机阀门（VALVE_OPEN / VALVE_CLOSE）；温度 > 28 自动开风扇 + 开阀门；串口上报新增 V: 阀门状态。
- **ESP32**：网关逻辑不变，仅把 MQTT 服务器地址改为当前树莓派 IP。
- **树莓派看板**（iot_dashboard）：解析 V:；新增阀门指令；网页改为 4 张状态卡 + 实时温度曲线。
- **语音助手**：新增「开阀门 / 关阀门」语音指令；温度回答带上阀门状态。
- **Grafana**：接入 SQLite 数据源，做工业级仪表盘 + 历史回溯。

## 二、MQTT 主题与指令
| 方向 | 主题 | 内容 |
| :--- | :--- | :--- |
| 上行 | home/fan/status | T:温度,H:湿度,F:风扇(0/1),V:阀门(0/1) |
| 下行 | pi/to/esp32 | FAN_ON / FAN_OFF / VALVE_OPEN / VALVE_CLOSE |

## 三、STM32 固件（DHT11 + 风扇 + 舵机阀门 + OLED）
```cpp
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

// --- 初始化对象 ---
DHT dht(DHTPIN, DHTTYPE);
Servo valveServo;
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);

// --- 状态变量 ---
bool isFanOn = false;
bool isValveOpen = false;
int lastButtonState = HIGH;

unsigned long lastDhtReadTime = 0;
unsigned long lastSerialSendTime = 0;
unsigned long lastOledUpdateTime = 0;
const long dhtInterval = 2000;
const long serialInterval = 2000;
const long oledInterval = 500;
float currentTemp = 0.0;
float currentHumi = 0.0;

void setup() {
  pinMode(AIN1, OUTPUT);
  pinMode(AIN2, OUTPUT);
  pinMode(PWMA, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  digitalWrite(AIN1, HIGH);
  digitalWrite(AIN2, LOW);
  analogWrite(PWMA, 0);

  Serial.begin(9600);
  Serial.setTimeout(10);
  dht.begin();

  u8g2.begin();
  u8g2.setFont(u8g2_font_ncenB08_tr);

  valveServo.attach(SERVO_PIN);
  valveServo.write(0);
  isValveOpen = false;

  Serial.println("Industrial System Booted...");
}

void loop() {
  unsigned long currentMillis = millis();

  // 任务1：本地物理按键（手动控制风扇）
  int currentButtonState = digitalRead(BUTTON_PIN);
  if (currentButtonState == LOW && lastButtonState == HIGH) {
    isFanOn = !isFanOn;
    analogWrite(PWMA, isFanOn ? 150 : 0);
    delay(50);
  }
  lastButtonState = currentButtonState;

  // 任务2：读取 DHT11 与自动控制（2 秒一次）
  if (currentMillis - lastDhtReadTime >= dhtInterval) {
    lastDhtReadTime = currentMillis;
    float t = dht.readTemperature();
    float h = dht.readHumidity();
    if (!isnan(t) && !isnan(h)) {
      currentTemp = t;
      currentHumi = h;
      // 温度 > 28 自动开阀门和风扇
      if (currentTemp > 28.0) {
        if (!isFanOn) { isFanOn = true; analogWrite(PWMA, 150); }
        if (!isValveOpen) { isValveOpen = true; valveServo.write(90); }
      } else {
        if (isFanOn) { isFanOn = false; analogWrite(PWMA, 0); }
        if (isValveOpen) { isValveOpen = false; valveServo.write(0); }
      }
    }
  }

  // 任务3：接收远程指令（含阀门）
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "FAN_ON")  { isFanOn = true;  analogWrite(PWMA, 150); }
    if (cmd == "FAN_OFF") { isFanOn = false; analogWrite(PWMA, 0); }
    if (cmd == "VALVE_OPEN")  { isValveOpen = true; valveServo.write(90); }
    if (cmd == "VALVE_CLOSE") { isValveOpen = false; valveServo.write(0); }
  }

  // 任务4：更新 OLED（500ms）
  if (currentMillis - lastOledUpdateTime >= oledInterval) {
    lastOledUpdateTime = currentMillis;
    u8g2.clearBuffer();
    u8g2.setCursor(0, 15); u8g2.print("Temp: "); u8g2.print(currentTemp); u8g2.print(" C");
    u8g2.setCursor(0, 30); u8g2.print("Humi: "); u8g2.print(currentHumi); u8g2.print(" %");
    u8g2.setCursor(0, 45); u8g2.print("Fan: "); u8g2.print(isFanOn ? "ON" : "OFF");
    u8g2.setCursor(0, 60); u8g2.print("Valve: "); u8g2.print(isValveOpen ? "OPEN" : "CLOSED");
    u8g2.sendBuffer();
  }

  // 任务5：串口上报（含阀门 V:）
  if (currentMillis - lastSerialSendTime >= serialInterval) {
    lastSerialSendTime = currentMillis;
    Serial.print("T:"); Serial.print(currentTemp);
    Serial.print(",H:"); Serial.print(currentHumi);
    Serial.print(",F:"); Serial.print(isFanOn ? 1 : 0);
    Serial.print(",V:"); Serial.println(isValveOpen ? 1 : 0);
  }
}
```

## 四、树莓派看板后端 iot_dashboard/app.py
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
_latest = {"temperature": None, "humidity": None, "fan": None, "valve": None, "ts": None}


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS readings ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts REAL NOT NULL,"
        " temperature REAL,"
        " humidity REAL,"
        " fan INTEGER,"
        " valve INTEGER)"
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(readings)").fetchall()]
    if "valve" not in cols:
        conn.execute("ALTER TABLE readings ADD COLUMN valve INTEGER")
    conn.commit()
    return conn


def insert_reading(temp, hum, fan, valve):
    with _lock:
        conn = _db()
        conn.execute(
            "INSERT INTO readings (ts, temperature, humidity, fan, valve) VALUES (?, ?, ?, ?, ?)",
            (time.time(), temp, hum, fan, valve),
        )
        conn.commit()
        conn.close()


def query_history(limit=500):
    with _lock:
        conn = _db()
        rows = conn.execute(
            "SELECT ts, temperature, humidity, fan, valve FROM readings ORDER BY id DESC LIMIT ?",
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


def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8", errors="replace").strip()
    print("IN " + msg.topic + ": " + payload)
    temp, hum, fan, valve = parse_sensor(payload)
    if all(x is None for x in (temp, hum, fan, valve)):
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
    _latest["ts"] = time.time()
    insert_reading(temp, hum, fan, valve)
    print("   温度=" + str(temp) + " 湿度=" + str(hum) + " 风扇=" + str(fan) + " 阀门=" + str(valve))


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
        {"ts": r[0] * 1000, "temperature": r[1], "humidity": r[2], "fan": r[3], "valve": r[4]}
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

## 五、网页看板 iot_dashboard/templates/index.html
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
  .cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
  .card { background: #1e293b; border-radius: 14px; padding: 14px 8px; text-align: center; }
  .card .label { font-size: 12px; color: #94a3b8; margin-bottom: 6px; }
  .card .value { font-size: 26px; font-weight: 700; }
  .card .unit { font-size: 13px; color: #94a3b8; }
  .temp .value { color: #fb923c; } .humi .value { color: #38bdf8; }
  .fan .value { color: #34d399; } .valve .value { color: #a78bfa; }
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
  </div>

  <div class="chart-box">
    <div class="chart-title">📈 温度实时曲线</div>
    <div id="chart"></div>
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
          chart.setOption({
            series: [
              { data: hist.map(function (p) { return [p.ts, p.temperature]; }) },
              { data: hist.map(function (p) { return [p.ts, p.humidity]; }) }
            ]
          });
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
  window.addEventListener('resize', function () { chart.resize(); });
</script>
</body>
</html>
```

## 六、语音助手阀门控制（voice_assistant.py 片段）
```python
# 阀门意图关键词
_VALVE_OPEN_WORDS  = ("开阀门", "打开阀门", "开启阀门", "开阀", "打开阀")
_VALVE_CLOSE_WORDS = ("关阀门", "关闭阀门", "关掉阀门", "关阀", "关闭阀")

# 下发阀门指令（舵机）
def set_valve(open_):
    cmd = "VALVE_OPEN" if open_ else "VALVE_CLOSE"
    if _mqtt_client is not None:
        _mqtt_client.publish(CMD_TOPIC, cmd)
        print(f"OUT {CMD_TOPIC}: {cmd}")
    return cmd

# answer() 中新增的阀门分支
    if any(w in text for w in _VALVE_OPEN_WORDS):
        set_valve(True)
        return "好的，阀门已经打开。"
    if any(w in text for w in _VALVE_CLOSE_WORDS):
        set_valve(False)
        return "好的，阀门已经关闭。"
    # 温度回答里带上阀门状态
    valve_txt = "阀门开着" if d["valve"] == 1 else "阀门关着"
    return f"现在是{_fmt(d['temperature'], '度')}，湿度{_fmt(d['humidity'], '%')}，{fan_txt}，{valve_txt}。"
```

## 七、Grafana 工业看板
### 数据源（SQLite 直连 sensor.db）
```yaml
apiVersion: 1
datasources:
  - name: SQLite
    type: frser-sqlite-datasource
    uid: sqlite-sensor
    access: proxy
    isDefault: true
    jsonData:
      path: /home/pi/deepseek_project/iot_dashboard/sensor.db
```

### 仪表盘 Provider
```yaml
apiVersion: 1
providers:
  - name: thea
    orgId: 1
    folder: ''
    type: file
    disableDeletion: false
    allowUiUpdates: true
    updateIntervalSeconds: 30
    options:
      path: /home/pi/opt/grafana/dashboards
```

### 仪表盘定义（温度/湿度曲线 + 状态卡）
```json
{
  "annotations": { "list": [] },
  "editable": true,
  "graphTooltip": 1,
  "panels": [
    {
      "id": 1,
      "type": "timeseries",
      "title": "温度 / 湿度 实时曲线",
      "datasource": { "type": "frser-sqlite-datasource", "uid": "sqlite-sensor" },
      "gridPos": { "h": 11, "w": 24, "x": 0, "y": 0 },
      "fieldConfig": {
        "defaults": { "custom": { "drawStyle": "line", "lineWidth": 2, "fillOpacity": 12, "showPoints": "never" } },
        "overrides": [
          { "matcher": { "id": "byName", "options": "温度" }, "properties": [ { "id": "custom.axisPlacement", "value": "left" }, { "id": "color", "value": { "mode": "fixed", "fixedColor": "orange" } } ] },
          { "matcher": { "id": "byName", "options": "湿度" }, "properties": [ { "id": "custom.axisPlacement", "value": "right" }, { "id": "color", "value": { "mode": "fixed", "fixedColor": "blue" } } ] }
        ]
      },
      "options": { "legend": { "displayMode": "list", "placement": "bottom" } },
      "targets": [
        {
          "refId": "A",
          "queryText": "SELECT ts AS time, temperature AS 温度, humidity AS 湿度 FROM readings ORDER BY ts",
          "timeColumns": ["time"],
          "format": "time_series"
        }
      ]
    },
    {
      "id": 2,
      "type": "stat",
      "title": "当前温度 (°C)",
      "datasource": { "type": "frser-sqlite-datasource", "uid": "sqlite-sensor" },
      "gridPos": { "h": 5, "w": 6, "x": 0, "y": 11 },
      "options": { "colorMode": "value", "graphMode": "area", "textMode": "value", "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false } },
      "fieldConfig": { "defaults": { "unit": "celsius", "decimals": 1 }, "overrides": [] },
      "targets": [ { "refId": "A", "queryText": "SELECT temperature AS 温度 FROM readings WHERE id = (SELECT MAX(id) FROM readings)", "format": "table" } ]
    },
    {
      "id": 3,
      "type": "stat",
      "title": "当前湿度 (%)",
      "datasource": { "type": "frser-sqlite-datasource", "uid": "sqlite-sensor" },
      "gridPos": { "h": 5, "w": 6, "x": 6, "y": 11 },
      "options": { "colorMode": "value", "graphMode": "area", "textMode": "value", "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false } },
      "fieldConfig": { "defaults": { "unit": "percent", "decimals": 1 }, "overrides": [] },
      "targets": [ { "refId": "A", "queryText": "SELECT humidity AS 湿度 FROM readings WHERE id = (SELECT MAX(id) FROM readings)", "format": "table" } ]
    },
    {
      "id": 4,
      "type": "stat",
      "title": "风扇 (1 开 / 0 关)",
      "datasource": { "type": "frser-sqlite-datasource", "uid": "sqlite-sensor" },
      "gridPos": { "h": 5, "w": 6, "x": 12, "y": 11 },
      "options": { "colorMode": "value", "textMode": "value", "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false } },
      "targets": [ { "refId": "A", "queryText": "SELECT fan AS 风扇 FROM readings WHERE id = (SELECT MAX(id) FROM readings)", "format": "table" } ]
    },
    {
      "id": 5,
      "type": "stat",
      "title": "阀门 (1 开 / 0 关)",
      "datasource": { "type": "frser-sqlite-datasource", "uid": "sqlite-sensor" },
      "gridPos": { "h": 5, "w": 6, "x": 18, "y": 11 },
      "options": { "colorMode": "value", "textMode": "value", "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false } },
      "targets": [ { "refId": "A", "queryText": "SELECT valve AS 阀门 FROM readings WHERE id = (SELECT MAX(id) FROM readings)", "format": "table" } ]
    }
  ],
  "refresh": "2s",
  "schemaVersion": 39,
  "templating": { "list": [] },
  "time": { "from": "now-15m", "to": "now" },
  "timezone": "browser",
  "title": "工业监控 · Thea",
  "uid": "thea-industrial",
  "version": 2
}
```

### 启动脚本
```bash
#!/usr/bin/env bash
# 启动 Grafana（用户空间）：解压 + 装 SQLite 插件 + 启动
set -e
G=$HOME/opt/grafana
DL=$HOME/opt/grafana-dl

if [ ! -x "$G/bin/grafana" ]; then
  tar xzf "$DL/grafana.tar.gz" -C "$G" --strip-components=1
fi

if [ ! -d "$G/data/plugins/frser-sqlite-datasource" ]; then
  "$G/bin/grafana" cli --homepath "$G" --pluginsDir "$G/data/plugins" plugins install frser-sqlite-datasource || true
fi

export GF_PATHS_HOME=$G
export GF_PATHS_DATA=$G/data
export GF_PATHS_LOGS=$G/logs
export GF_PATHS_PLUGINS=$G/data/plugins
export GF_PATHS_PROVISIONING=$G/conf/provisioning
export GF_SERVER_HTTP_ADDR=0.0.0.0
export GF_SERVER_HTTP_PORT=3000
export GF_SECURITY_ADMIN_USER=admin
export GF_SECURITY_ADMIN_PASSWORD=admin
export GF_AUTH_ANONYMOUS_ENABLED=true
export GF_AUTH_ANONYMOUS_ORG_ROLE=Admin

pkill -f "$G/bin/grafana server" 2>/dev/null || true
sleep 1
setsid "$G/bin/grafana" server --homepath "$G" > "$G/grafana.log" 2>&1 < /dev/null &
echo "grafana started pid $!"
```

## 八、Grafana 避坑
1. Grafana 官方不支持 SQLite，需社区插件 frser-sqlite-datasource（aarch64 有现成二进制）。
2. 该插件要求**时间列**为数字(unix 秒)或 RFC3339；用 ts AS time + 查询里 timeColumns:[time] 才被识别为时间，否则曲线为空。
3. dl.grafana.com 国内单线程约 12KB/s，用 8 路并行分块下载可提速到约 160KB/s。
4. 全过程用户空间（免 sudo），用 setsid 启动才能脱离会话常驻。
5. Grafana 只是读数；**数据入库靠 Flask 看板**，所以看板要一直开着。

## 九、运行方式
```bash
cd ~/deepseek_project/iot_dashboard && source ../venv/bin/activate && python3 app.py    # 看板 + 写库
bash ~/opt/grafana-setup.sh                               # 启动 Grafana (http://<树莓派IP>:3000)
DISPLAY=:0 python3 ~/deepseek_project/voice_assistant_gui.py                                                       # 语音助手
```
