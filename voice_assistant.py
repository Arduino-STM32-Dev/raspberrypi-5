import os
import dashscope
import sherpa_onnx
import soundfile as sf
from openai import OpenAI

# ================= 配置区 =================
# 【必改 1】你的阿里云百炼 API Key（用于语音识别）
DASHSCOPE_API_KEY = ""
dashscope.api_key = DASHSCOPE_API_KEY

# 【必改 2】你的 DeepSeek 官方 API Key（用于对话思考）
DEEPSEEK_API_KEY = ""
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 【音频设备】你的 USB 声卡（麦克风和扬声器都在 card 2）
MIC_DEVICE = "plughw:2,0"      # 录音设备
SPEAKER_DEVICE = "plughw:2,0"  # 播放设备

# 【TTS 模型路径】本地 sherpa-onnx 模型目录
TTS_MODEL_DIR = "/home/pi/deepseek_project/sherpa-onnx-vits-zh-ll"
# ==========================================

# ================= 初始化组件 =================
# 1. 初始化 DeepSeek 客户端
llm_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

# 2. 初始化本地 TTS 引擎（只需加载一次，常驻内存）
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
# ==========================================


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
    """调用 DeepSeek Flash 模型进行对话"""
    print("🧠 DeepSeek Flash 正在思考...")
    try:
        response = llm_client.chat.completions.create(
            model="deepseek-flash",  # 官方最新模型
            messages=[
                {
                    "role": "system",
                    "content": "你是一个运行在树莓派上的桌面语音助手。回答要口语化、简短直接，不超过两句话，不要包含表情符号和特殊符号，适合语音播报。"
                },
                {"role": "user", "content": user_text}
            ],
            max_tokens=500,     # 适当放宽，防止思考过程被截断
            temperature=0.7,
            stream=False
        )
        
        if not response.choices or not response.choices[0].message.content:
            print("⚠️ API 返回空内容")
            return "抱歉，我好像走神了，请再说一遍。"
            
        reply = response.choices[0].message.content.strip()
        return reply
    except Exception as e:
        print(f"❌ 模型推理失败: {e}")
        return None


def speak(text):
    """使用 sherpa-onnx 本地合成语音并播放"""
    print(f"🔊 助手播报: {text}")

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
            reply_text = chat_with_deepseek(user_text)
            if not reply_text:
                continue

            # 4. 本地 TTS 播报
            speak(reply_text)

        except Exception as e:
            print(f"⚠️ 流程出错: {e}")
