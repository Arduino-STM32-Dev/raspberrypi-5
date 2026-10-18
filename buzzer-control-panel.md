# ESP32 蜂鸣器 Web 控制面板

树莓派上的轻量面板：浏览器调音量、看 STM32 实时数据、发任意透传指令。

> 固件协议与音量原理见 `esp32` 仓库的 **buzzer-alarm-edge.md**。

## 1. 依赖

```bash
cd /home/pi/deepseek_project
./venv/bin/pip install flask paho-mqtt
```

## 2. 启动

```bash
cd /home/pi/deepseek_project/esp32_buzzer
setsid ./venv/bin/python3 app.py > /tmp/buzzer.log 2>&1 < /dev/null &
```

监听 `0.0.0.0:5002`，打开 `http://<树莓派IP>:5002`。

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MQTT_BROKER` | localhost | broker 地址 |
| `MQTT_PORT` | 1883 | |
| `PUB_TOPIC` | pi/to/esp32 | 下发 |
| `SUB_TOPIC` | home/fan/status | 上行 |
| `PORT` | 5002 | HTTP 端口 |

## 3. 接口

| 方法 | 路径 | 请求体 | 下发 |
|---|---|---|---|
| POST | `/api/buzzer` | `{"volume":0-100}` | `BUZZER:n` |
| POST | `/api/raw` | `{"cmd":"LED_ON"}` | 原样透传 |
| GET | `/api/status` | — | 连接状态、最近指令、最近消息 |

## 4. 开机自启（可选，免 sudo）

```bash
crontab -e
# 追加
@reboot cd /home/pi/deepseek_project/esp32_buzzer && setsid ./venv/bin/python3 app.py > /tmp/buzzer.log 2>&1 < /dev/null &
```

## 5. 验证

```bash
curl -s http://127.0.0.1:5002/api/status
curl -s -X POST -H 'Content-Type: application/json' -d '{"volume":60}' http://127.0.0.1:5002/api/buzzer
mosquitto_sub -h 127.0.0.1 -t 'pi/to/esp32' -v
```

## 6. 注意

- 面板**无鉴权**，仅限内网。
- 面板只转发指令。应急判定放控制器里（见 `buzzer-alarm-edge.md` 第 6 节）。
