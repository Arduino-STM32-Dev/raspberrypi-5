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
