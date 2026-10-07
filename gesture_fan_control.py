#!/usr/bin/env python3
"""手势控制风扇：摄像头识别 ✌️(Victory) 手势 -> MQTT 开关风扇。

链路：摄像头 -> MediaPipe GestureRecognizer -> MQTT(pi/to/esp32) -> ESP32 -> UART -> STM32 风扇
- 比 ✌️(Victory)  => 发 FAN_ON
- 放下手         => 发 FAN_OFF
- 2 秒防抖；窗口内显示提示与实时状态；按 q 退出
"""

import os
import time

import cv2
import mediapipe as mp
import paho.mqtt.client as mqtt
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions

# 1. MQTT 配置
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
CMD_TOPIC = "pi/to/esp32"
CONF = 0.4  # 手势置信度阈值

try:
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "Pi_Vision")
except (AttributeError, TypeError):
    mqtt_client = mqtt.Client("Pi_Vision")
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

# 2. MediaPipe GestureRecognizer（新版 Tasks API，自带 Victory 手势）
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "gesture_recognizer.task")
base_options = BaseOptions(model_asset_path=MODEL_PATH)
options = vision.GestureRecognizerOptions(base_options=base_options, num_hands=1)
recognizer = vision.GestureRecognizer.create_from_options(options)

# 3. 摄像头
cap = cv2.VideoCapture(0)

fan_state = "OFF"
last_gesture_time = 0
DEBOUNCE = 2.0
last_console = 0


def put(img, text, y, color=(255, 255, 255), scale=0.6, thick=1):
    cv2.putText(img, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def main():
    global fan_state, last_gesture_time, last_console

    while cap.isOpened():
        ok, frame = cap.read()
        if not ok:
            continue

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = recognizer.recognize(mp_image)

        gesture_name = "none"
        victory = False
        if result.gestures:
            top = result.gestures[0][0]
            gesture_name = top.category_name
            if top.category_name == "Victory" and top.score > CONF:
                victory = True

        # 4. 状态机（2 秒防抖）
        now = time.time()
        if victory and fan_state == "OFF" and (now - last_gesture_time > DEBOUNCE):
            fan_state = "ON"
            mqtt_client.publish(CMD_TOPIC, "FAN_ON")
            print("📤 发送指令: FAN_ON")
            last_gesture_time = now
        elif not victory and fan_state == "ON" and (now - last_gesture_time > DEBOUNCE):
            fan_state = "OFF"
            mqtt_client.publish(CMD_TOPIC, "FAN_OFF")
            print("📤 发送指令: FAN_OFF")
            last_gesture_time = now

        # 5. 画面提示（HUD）
        put(frame, "Gesture Fan Control", 28, (255, 255, 0), 0.7, 2)
        put(frame, "Hand : " + gesture_name, 56, (200, 200, 200))
        fan_color = (0, 255, 0) if fan_state == "ON" else (0, 0, 255)
        put(frame, "FAN  : " + fan_state, 84, fan_color, 0.7, 2)

        if victory:
            put(frame, "VICTORY!  FAN ON", 122, (0, 255, 0), 0.9, 2)
        else:
            put(frame, "Show a V-sign (Victory) to turn FAN ON", 122, (0, 255, 255), 0.6)

        h = frame.shape[0]
        put(frame, "V-sign = FAN ON  |  Drop hand = FAN OFF  |  q = quit", h - 14, (170, 170, 170), 0.5)

        cv2.imshow("Gesture Control", frame)

        # 6. 终端反馈（每 2 秒）
        if now - last_console > 2:
            print("👀 看到手势: " + gesture_name + " | 风扇: " + fan_state)
            last_console = now

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    mqtt_client.loop_stop()


if __name__ == "__main__":
    main()
