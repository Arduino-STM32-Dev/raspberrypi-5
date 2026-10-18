#!/usr/bin/env python3
"""ESP32 蜂鸣器 Web 控制面板（音量版）。

- 发布 pi/to/esp32 : "BUZZER:<0-100>"（0=静音，100=最大）
- 订阅 home/fan/status 显示 STM32 实时数据
- 端口 5002

启动：
  cd /home/pi/deepseek_project/esp32_buzzer
  setsid /home/pi/deepseek_project/venv/bin/python3 app.py > /tmp/buzzer.log 2>&1 < /dev/null &
"""

import os
import threading
import time
from collections import deque

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, render_template, request

MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
PUB_TOPIC = os.environ.get("PUB_TOPIC", "pi/to/esp32")
SUB_TOPIC = os.environ.get("SUB_TOPIC", "home/fan/status")
PORT = int(os.environ.get("PORT", "5002"))

app = Flask(__name__)
lock = threading.Lock()
state = {
    "connected": False,
    "volume": None,
    "last_cmd": None,
    "last_cmd_at": None,
    "last_status": None,
    "last_status_at": None,
    "messages": deque(maxlen=30),
}

client = mqtt.Client(client_id="pi_buzzer_panel")


def _log(msg):
    print(msg, flush=True)


def on_connect(c, userdata, flags, rc, *args):
    ok = (rc == 0)
    with lock:
        state["connected"] = ok
    if ok:
        c.subscribe(SUB_TOPIC, qos=0)
        _log("MQTT connected, subscribed " + SUB_TOPIC)
    else:
        _log("MQTT connect failed rc=" + str(rc))


def on_disconnect(c, userdata, rc, *args):
    with lock:
        state["connected"] = False
    _log("MQTT disconnected rc=" + str(rc))


def on_message(c, userdata, msg):
    payload = msg.payload.decode("utf-8", "replace")
    now = time.time()
    with lock:
        state["last_status"] = payload
        state["last_status_at"] = now
        state["messages"].appendleft({"t": now, "topic": msg.topic, "payload": payload[:200]})


client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message


def mqtt_loop():
    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, keepalive=30)
            client.loop_forever(retry_first_connection=False)
        except Exception as e:  # noqa: BLE001
            _log("MQTT error: " + str(e))
        time.sleep(3)


def _send(cmd, volume=None):
    with lock:
        connected = state["connected"]
    if not connected:
        return jsonify({"ok": False, "error": "MQTT 未连接"}), 503

    info = client.publish(PUB_TOPIC, cmd, qos=1)
    try:
        info.wait_for_publish(timeout=2)
    except Exception:  # noqa: BLE001
        pass

    try:
        ok = bool(info.is_published())
    except Exception:  # noqa: BLE001
        ok = False

    with lock:
        state["last_cmd"] = cmd
        state["last_cmd_at"] = time.time()
        if volume is not None:
            state["volume"] = volume

    if not ok:
        return jsonify({"ok": False, "error": "发布超时"}), 502
    _log("PUB " + PUB_TOPIC + " <- " + cmd)
    return jsonify({"ok": True, "cmd": cmd, "topic": PUB_TOPIC})


@app.route("/")
def index():
    return render_template("index.html", pub=PUB_TOPIC, sub=SUB_TOPIC)


@app.route("/api/status")
def api_status():
    with lock:
        return jsonify({
            "mqtt_connected": state["connected"],
            "broker": MQTT_BROKER + ":" + str(MQTT_PORT),
            "pub_topic": PUB_TOPIC,
            "sub_topic": SUB_TOPIC,
            "volume": state["volume"],
            "last_cmd": state["last_cmd"],
            "last_cmd_at": state["last_cmd_at"],
            "last_status": state["last_status"],
            "last_status_at": state["last_status_at"],
            "messages": list(state["messages"]),
        })


@app.route("/api/buzzer", methods=["POST"])
def api_buzzer():
    data = request.get_json(silent=True) or {}
    try:
        vol = int(data.get("volume"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "volume 必须是 0-100 的整数"}), 400
    if not 0 <= vol <= 100:
        return jsonify({"ok": False, "error": "volume 超出 0-100"}), 400
    return _send("BUZZER:%d" % vol, volume=vol)


@app.route("/api/raw", methods=["POST"])
def api_raw():
    data = request.get_json(silent=True) or {}
    cmd = (data.get("cmd") or "").strip()
    if not cmd:
        return jsonify({"ok": False, "error": "cmd 不能为空"}), 400
    if len(cmd) > 200:
        return jsonify({"ok": False, "error": "cmd 过长"}), 400
    return _send(cmd)


if __name__ == "__main__":
    threading.Thread(target=mqtt_loop, daemon=True).start()
    _log("Buzzer panel on http://0.0.0.0:" + str(PORT) + "  pub=" + PUB_TOPIC)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
