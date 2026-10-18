# ESP32 蜂鸣器 Web 控制面板

树莓派上跑的轻量面板：浏览器调音量、切警报/音乐模式、看 STM32 实时数据。

> 固件侧协议与音量原理见 `esp32` 仓库的 **buzzer-alarm-edge.md**。

## 1. 依赖

Flask + paho-mqtt。用项目 venv：

```bash
cd /home/pi/deepseek_project
./venv/bin/pip install flask paho-mqtt
```

## 2. 启动

```bash
cd /home/pi/deepseek_project/esp32_buzzer
setsid ./venv/bin/python3 app.py > /tmp/buzzer.log 2>&1 < /dev/null &
```

默认监听 `0.0.0.0:5002`，浏览器打开 `http://<树莓派IP>:5002`。

环境变量可覆盖：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MQTT_BROKER` | localhost | broker 地址 |
| `MQTT_PORT` | 1883 | |
| `PUB_TOPIC` | pi/to/esp32 | 下发指令 |
| `SUB_TOPIC` | home/fan/status | 接收 STM32 数据 |
| `PORT` | 5002 | HTTP 端口 |

## 3. 接口

| 方法 | 路径 | 请求体 | 下发 |
|---|---|---|---|
| POST | `/api/buzzer` | `{"volume":0-100}` | `BUZZER:n`（非 idle 时先补 `ALARM_OFF`） |
| POST | `/api/alarm` | `{"on":true\|false}` | `ALARM_ON` / `ALARM_OFF` |
| POST | `/api/music` | `{"action":"play"\|"stop"}` | `MUSIC_PLAY` / `ALARM_OFF` |
| POST | `/api/raw` | `{"cmd":"LED_ON"}` | 原样透传 |
| GET | `/api/status` | — | 连接状态、当前模式、最近消息 |

### 为什么 `/api/buzzer` 有时发两条

固件在 ALARM/MUSIC 模式下会周期性覆盖 PWM。面板检测到非 idle 时，先发 `ALARM_OFF` 再发 `BUZZER:n`，保证音量生效。固件修复后这条冗余指令也无害。

## 4. 开机自启（可选，免 sudo）

```bash
crontab -e
# 追加一行
@reboot cd /home/pi/deepseek_project/esp32_buzzer && setsid ./venv/bin/python3 app.py > /tmp/buzzer.log 2>&1 < /dev/null &
```

## 5. 验证

```bash
curl -s http://127.0.0.1:5002/api/status
curl -s -X POST -H 'Content-Type: application/json' -d '{"volume":60}' http://127.0.0.1:5002/api/buzzer
```

订阅侧确认：

```bash
mosquitto_sub -h 127.0.0.1 -t 'pi/to/esp32' -v
```

## 6. 注意

- 面板**无鉴权**，仅限内网。要暴露到公网请加反向代理 + Basic Auth。
- 面板只负责转发指令，真正的应急判定应放在控制器里（见 `buzzer-alarm-edge.md` 第 7 节）。
