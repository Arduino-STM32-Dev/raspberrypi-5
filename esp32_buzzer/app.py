#!/usr/bin/env python3
"""ESP32 蜂鸣器 Web 控制面板 v2（支持警报 / 音乐模式）。

指令（发到 pi/to/esp32）：
  BUZZER:<0-100>   音量（0=静音，100=最大）
  ALARM_ON         警报节奏
  ALARM_OFF        停止并回到 IDLE
  MUSIC_PLAY       循环旋律
  其他             原样透传 STM32

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
    "mode": "idle",          # idle | alarm | music
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


def _publish(cmd):
    info = client.publish(PUB_TOPIC, cmd, qos=1)
    try:
        info.wait_for_publish(timeout=2)
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(info.is_published())
    except Exception:  # noqa: BLE001
        return False


def _send_many(cmds, volume=None, mode=None):
    with lock:
        connected = state["connected"]
    if not connected:
        return jsonify({"ok": False, "error": "MQTT 未连接"}), 503

    sent = []
    for cmd in cmds:
        if not _publish(cmd):
            return jsonify({"ok": False, "error": "发布超时: " + cmd}), 502
        sent.append(cmd)
        _log("PUB " + PUB_TOPIC + " <- " + cmd)

    with lock:
        state["last_cmd"] = cmds[-1]
        state["last_cmd_at"] = time.time()
        if volume is not None:
            state["volume"] = volume
        if mode is not None:
            state["mode"] = mode

    return jsonify({"ok": True, "sent": sent, "topic": PUB_TOPIC})


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
            "mode": state["mode"],
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
    # 先 ALARM_OFF 退出警报/音乐模式（当前固件里 BUZZER: 不重置模式），再设音量
    with lock:
        mode = state["mode"]
    cmds = ["BUZZER:%d" % vol]
    if mode != "idle":
        cmds = ["ALARM_OFF", "BUZZER:%d" % vol]
    return _send_many(cmds, volume=vol, mode="idle")


@app.route("/api/alarm", methods=["POST"])
def api_alarm():
    data = request.get_json(silent=True) or {}
    on = bool(data.get("on", True))
    if on:
        return _send_many(["ALARM_ON"], mode="alarm")
    return _send_many(["ALARM_OFF"], mode="idle")


@app.route("/api/music", methods=["POST"])
def api_music():
    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "play").strip().lower()
    if action == "play":
        return _send_many(["MUSIC_PLAY"], mode="music")
    if action in ("stop", "off"):
        # 固件里没有 MUSIC_STOP，ALARM_OFF 会把模式置回 IDLE
        return _send_many(["ALARM_OFF"], mode="idle")
    return jsonify({"ok": False, "error": "action 只支持 play / stop"}), 400


@app.route("/api/raw", methods=["POST"])
def api_raw():
    data = request.get_json(silent=True) or {}
    cmd = (data.get("cmd") or "").strip()
    if not cmd:
        return jsonify({"ok": False, "error": "cmd 不能为空"}), 400
    if len(cmd) > 200:
        return jsonify({"ok": False, "error": "cmd 过长"}), 400
    return _send_many([cmd])


if __name__ == "__main__":
    threading.Thread(target=mqtt_loop, daemon=True).start()
    _log("Buzzer panel v2 on http://0.0.0.0:" + str(PORT) + "  pub=" + PUB_TOPIC)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
