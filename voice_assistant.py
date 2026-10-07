import os
# 从同目录 .env 读取环境变量（若存在；.env 已 gitignore，勿提交）
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    for _line in open(_env_path, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

import re
import threading
import time

import requests

# dashscope 用于语音识别；Android 无录音功能，做成可选导入（桌面端仍可用）。
try:
    import dashscope
    HAS_ASR = True
except ImportError:
    dashscope = None
    HAS_ASR = False

# sherpa-onnx 与 soundfile 是桌面 Linux 专属依赖，Android 上不可用。
# 用 try/except 包裹，让模块在 Android 也能导入（本地 TTS 降级为不可用）。
try:
    import sherpa_onnx
    import soundfile as sf
    HAS_LOCAL_TTS = True
except ImportError:
    sherpa_onnx = None
    sf = None
    HAS_LOCAL_TTS = False

# ================= 配置区 =================
# 【必改 1】你的阿里云百炼 API Key（用于语音识别）
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
if HAS_ASR:
    dashscope.api_key = DASHSCOPE_API_KEY

# 【必改 2】你的 DeepSeek 官方 API Key（用于对话思考）
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 【音频设备】你的 USB 声卡（麦克风和扬声器都在 card 2）
MIC_DEVICE = "plughw:2,0"      # 录音设备
SPEAKER_DEVICE = "plughw:2,0"  # 播放设备

# 【TTS 模型路径】本地 sherpa-onnx 模型目录
TTS_MODEL_DIR = "/home/pi/deepseek_project/sherpa-onnx-vits-zh-ll"
# ==========================================

# ================= 初始化组件 =================
# DeepSeek 对话改用 requests 直连（见 chat_with_deepseek），
# 避免 openai SDK 引入 pydantic(Rust) 导致 Android 打包失败。

# 2. 初始化本地 TTS 引擎（仅桌面 Linux 可用；Android 上自动降级）
tts = None
if HAS_LOCAL_TTS:
    print("⏳ 正在加载本地 TTS 模型，请稍候...")
    tts_config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=os.path.join(TTS_MODEL_DIR, "model.onnx"),
                tokens=os.path.join(TTS_MODEL_DIR, "tokens.txt"),
                lexicon=os.path.join(TTS_MODEL_DIR, "lexicon.txt"),
            ),
        ),
        rule_fsts=os.path.join(TTS_MODEL_DIR, "phone.fst"),
        max_num_sentences=1,
    )
    tts = sherpa_onnx.OfflineTts(tts_config)
    print("✅ 本地 TTS 模型加载成功！")
else:
    print("⚠️ 本地 TTS 不可用（sherpa-onnx 未安装），将跳过语音播报。")
# ==========================================

# ================= 传感器 / 风扇（MQTT） =================
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
SENSOR_TOPIC = "home/fan/status"   # ESP32 上行：T:..,H:..,F:..
CMD_TOPIC = "pi/to/esp32"          # 下行指令（需 ESP32 订阅后生效）

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except ImportError:
    mqtt = None
    HAS_MQTT = False

_sensor = {"temperature": None, "humidity": None, "fan": None}
_sensor_lock = threading.Lock()
_mqtt_client = None


def _parse_sensor(payload):
    t = h = f = None
    for k, v in re.findall(r"([A-Za-z])\s*[:=]\s*(-?\d+(?:\.\d+)?)", payload):
        k = k.lower()
        if k == "t":
            t = float(v)
        elif k == "h":
            h = float(v)
        elif k == "f":
            f = int(float(v))
    return t, h, f


def _on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(SENSOR_TOPIC)
        print(f"✅ 已订阅传感器主题 {SENSOR_TOPIC}")


def _on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8", errors="replace").strip()
    t, h, f = _parse_sensor(payload)
    with _sensor_lock:
        if t is not None:
            _sensor["temperature"] = t
        if h is not None:
            _sensor["humidity"] = h
        if f is not None:
            _sensor["fan"] = f


def _mqtt_loop():
    global _mqtt_client
    while True:
        try:
            _mqtt_client = mqtt.Client(client_id="voice_assistant")
            _mqtt_client.on_connect = _on_connect
            _mqtt_client.on_message = _on_message
            _mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            _mqtt_client.loop_forever()
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 传感器 MQTT 异常，5 秒后重试: {e}")
            time.sleep(5)


def get_sensor_data():
    with _sensor_lock:
        return dict(_sensor)


def set_fan(on):
    """开/关风扇，返回下发的指令。"""
    cmd = "FAN_ON" if on else "FAN_OFF"
    if _mqtt_client is not None:
        _mqtt_client.publish(CMD_TOPIC, cmd)
        print(f"📤 {CMD_TOPIC}: {cmd}")
    else:
        print("⚠️ MQTT 未连接，无法下发风扇指令")
    return cmd


if HAS_MQTT:
    threading.Thread(target=_mqtt_loop, daemon=True).start()
# ==========================================================


def record_audio(duration=5):
    """录制音频（强制指定 USB 麦克风设备）"""
    print(f"🎙️ 请说话（录制 {duration} 秒）...")

    if os.path.exists("/tmp/input.wav"):
        os.remove("/tmp/input.wav")

    cmd = f"arecord -D {MIC_DEVICE} -d {duration} -f S16_LE -r 16000 -c 1 /tmp/input.wav"
    ret = os.system(cmd)

    if ret != 0 or not os.path.exists("/tmp/input.wav"):
        print("❌ 录音失败，请检查麦克风连接")
        return None

    print("✅ 录音结束")
    return "/tmp/input.wav"


def speech_to_text(audio_path):
    """使用 qwen3-asr-flash 识别本地语音"""
    if not HAS_ASR:
        print("⚠️ 语音识别不可用（未安装 dashscope）")
        return None
    print("📝 正在识别语音...")
    try:
        abs_path = os.path.abspath(audio_path)
        messages = [
            {
                "role": "user",
                "content": [
                    {"audio": f"file://{abs_path}"}
                ]
            }
        ]

        response = dashscope.MultiModalConversation.call(
            model="qwen3-asr-flash",
            messages=messages,
            result_format="message",
            asr_options={"language": "zh", "enable_itn": False}
        )

        if response.status_code == 200:
            return response.output.choices[0].message.content[0]["text"]
        else:
            print(f"❌ 语音识别接口报错: {response}")
            return None
    except Exception as e:
        print(f"❌ 语音识别异常: {e}")
        return None


def chat_with_deepseek(user_text):
    """调用 DeepSeek 模型进行对话（requests 直连，兼容桌面与 Android）"""
    print("🧠 DeepSeek 正在思考...")
    d = get_sensor_data()
    fan_txt = "开" if d["fan"] == 1 else "关"
    sys_prompt = (
        "你是一个运行在树莓派上的桌面语音助手。回答要口语化、简短直接，"
        "不超过两句话，不要包含表情符号和特殊符号，适合语音播报。"
        f"当前传感器读数：温度 {d['temperature']} 度，湿度 {d['humidity']}%，风扇{fan_txt}。"
    )
    try:
        resp = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-flash",
                "messages": [
                    {
                        "role": "system",
                        "content": sys_prompt
                    },
                    {"role": "user", "content": user_text}
                ],
                "max_tokens": 500,
                "temperature": 0.7,
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        reply = data["choices"][0]["message"]["content"].strip()
        if not reply:
            return "抱歉，我好像走神了，请再说一遍。"
        return reply
    except Exception as e:
        print(f"❌ 模型推理失败: {e}")
        return None


# 意图关键词
_TEMP_WORDS = ("温度", "多少度", "几度", "湿度", "温湿度", "热不热", "冷不冷", "传感器", "温湿")
_FAN_ON_WORDS = ("开风扇", "打开风扇", "开启风扇", "开一下风扇", "启动风扇", "开吹风")
_FAN_OFF_WORDS = ("关风扇", "关闭风扇", "关掉风扇", "停止风扇", "停风扇", "关吹风")


def _fmt(x, unit=""):
    return "--" if x is None else f"{x}{unit}"


def answer(user_text):
    """路由：风扇开关 / 温湿度查询 / 其它交给 DeepSeek。"""
    text = user_text.strip()
    if any(w in text for w in _FAN_ON_WORDS):
        set_fan(True)
        return "好的，风扇已经打开。"
    if any(w in text for w in _FAN_OFF_WORDS):
        set_fan(False)
        return "好的，风扇已经关闭。"
    if any(w in text for w in _TEMP_WORDS):
        d = get_sensor_data()
        if d["temperature"] is None and d["humidity"] is None:
            return "抱歉，我暂时读不到传感器数据。"
        fan_txt = "风扇开着" if d["fan"] == 1 else "风扇关着"
        return f"现在是{_fmt(d['temperature'], '度')}，湿度{_fmt(d['humidity'], '%')}，{fan_txt}。"
    return chat_with_deepseek(user_text)


def speak(text):
    """使用 sherpa-onnx 本地合成语音并播放"""
    print(f"🔊 助手播报: {text}")

    if not HAS_LOCAL_TTS:
        print("⚠️ 本地 TTS 不可用，跳过播报（仅显示文字）。")
        return

    clean_text = text.replace('"', "").replace("'", "").replace("\n", " ").strip()
    if not clean_text:
        return

    try:
        # 1. 生成音频
        audio = tts.generate(clean_text, sid=0, speed=1.0)
        if len(audio.samples) == 0:
            print("❌ TTS 合成失败")
            return

        # 2. 保存为 WAV 文件
        output_file = "/tmp/reply.wav"
        sf.write(output_file, audio.samples, samplerate=audio.sample_rate)

        # 3. 播放（使用你测试成功的 USB 声卡设备号）
        os.system(f"aplay -D {SPEAKER_DEVICE} {output_file} -q")

    except Exception as e:
        print(f"❌ TTS 合成异常: {e}")


if __name__ == "__main__":
    print("🤖 语音助手已启动！")
    while True:
        cmd = input("\n按回车键开始说话（输入 q 退出）: ").strip().lower()
        if cmd == "q":
            print("👋 再见！")
            break
        try:
            # 1. 录音
            audio_path = record_audio(duration=5)
            if not audio_path:
                continue

            # 2. 语音转文字
            user_text = speech_to_text(audio_path)
            if not user_text:
                continue
            print(f"👤 你说: {user_text}")

            # 3. 大模型思考
            reply_text = answer(user_text)
            if not reply_text:
                continue

            # 4. 本地 TTS 播报
            speak(reply_text)

        except Exception as e:
            print(f"⚠️ 流程出错: {e}")
