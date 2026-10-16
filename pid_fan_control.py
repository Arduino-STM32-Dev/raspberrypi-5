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
