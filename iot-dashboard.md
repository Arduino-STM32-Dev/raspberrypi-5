# 树莓派 IoT 监控看板 + 语音助手 GUI

> **承接本仓库「基础环境 → 视觉/语音应用」链路**：在环境搭好的基础上，把 STM32 采集的温湿度、风扇状态经 ESP32 送到树莓派，
> 做一个手机/电脑都能访问的实时网页看板，并让桌面语音助手支持「问温度」「开关风扇」。
>
> 适用链路：Raspberry Pi 5 + ESP32 + STM32F103。

---

## 一、整体架构

    Pi(树莓派5)  ──MQTT──▶  ESP32  ──UART──▶  STM32F103 (DHT11 + 风扇)
        ▲                                          │
        └────────── MQTT( home/fan/status ) ◀──────┘

- STM32：每 2 秒通过串口上报一行 T:温度,H:湿度,F:风扇(0/1)
- ESP32：WiFi 网关，把 STM32 串口数据发布到 MQTT 主题 home/fan/status
- 树莓派：mosquitto 作 broker；Flask 看板订阅并展示；语音助手查询与控制

---

## 二、本仓库新增文件

| 文件 | 说明 |
| :--- | :--- |
| voice_assistant_gui.py | 语音助手桌面界面（PyQt6） |
| voice_assistant.py | 助手核心：录音/识别/对话/播报 + 温湿度查询 + 风扇控制 |
| iot_dashboard/app.py | Flask + paho-mqtt + SQLite 后端 |
| iot_dashboard/templates/index.html | ECharts 实时看板页面 |
| iot_dashboard/requirements.txt | 看板依赖 |

---

## 三、环境变量（重要）

先复制模板并填入自己的 Key：

    cp .env.example .env

.env 已在 .gitignore 中忽略，切勿提交真实密钥。程序会自动读取同目录 .env（也支持直接 export）。

---

## 四、安装依赖

    sudo apt-get update && sudo apt-get install -y mosquitto mosquitto-clients

    cd ~/deepseek_project
    python3 -m venv venv && source venv/bin/activate
    pip install -r iot_dashboard/requirements.txt
    pip install PyQt6 dashscope openai sherpa-onnx soundfile requests paho-mqtt

---

## 五、运行

网页看板：

    cd ~/deepseek_project
    source venv/bin/activate
    python3 iot_dashboard/app.py

浏览器打开 http://<树莓派IP>:5000

语音助手 GUI（显示在树莓派接的显示器上）：

    DISPLAY=:0 python3 voice_assistant_gui.py

---

## 六、怎么用

- 看板：三张大卡显示 温度 / 湿度 / 风扇状态，下面是实时曲线；底部「开风扇 / 关风扇」按钮下发指令。
- 语音助手：
  - 说/输入「现在温度多少」「湿度多少」→ 用实时数据回答
  - 说/输入「开风扇」「关风扇」→ 下发控制指令
  - 其它问题 → 交给 DeepSeek 对话

---

## 七、MQTT 主题约定

| 方向 | 主题 | 内容 |
| :--- | :--- | :--- |
| 上行 | home/fan/status | ESP32 → Pi，如 T:25.0,H:60.0,F:1 |
| 下行 | pi/to/esp32 | Pi → ESP32，FAN_ON / FAN_OFF |

注意：想让看板/语音的「开/关风扇」真正生效，ESP32 需订阅 pi/to/esp32 并把指令转发给 STM32，
STM32 再按 FAN_ON / FAN_OFF 控制风扇。若固件未订阅，按钮只会发出指令、设备不动。

---

## 八、快速验证

    mosquitto_sub -h localhost -t home/fan/status -v
    mosquitto_pub -h localhost -t pi/to/esp32 -m FAN_ON
