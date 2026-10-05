import os
import base64
from openai import OpenAI

# ================= 配置区 =================
API_KEY = ""   # 【记得替换】
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen-vl-plus"  # 如果报模型不存在，改成 "qwen-vl-max-latest"
IMAGE_PATH = "/tmp/cam.jpg"
# ==========================================

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

def take_photo():
    print("📸 正在拍照...")
    ret = os.system(f"fswebcam -r 1280x720 --no-banner {IMAGE_PATH}")
    if ret != 0:
        raise RuntimeError("拍照失败，请检查摄像头是否插好")
    return IMAGE_PATH

def encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def identify_object(image_path):
    print("🧠 正在识别物体...")
    base64_image = encode_image(image_path)
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                        },
                        {
                            "type": "text",
                            "text": "请用一句简短、口语化的中文描述这张图片里的主要物品，适合语音播报，不要包含特殊符号。",
                        },
                    ],
                }
            ],
            max_tokens=100,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ 识别失败: {e}")
        return None

def speak(text):
    print(f"🔊 正在播报: {text}")
    clean_text = text.replace('"', "").replace("'", "").replace("\n", " ").replace("，", ",")
    os.system(
        f'edge-tts --voice zh-CN-XiaoxiaoNeural --text "{clean_text}" '
        f'--write-media /tmp/reply.mp3'
    )
    os.system("mpg123 -q /tmp/reply.mp3")

if __name__ == "__main__":
    while True:
        cmd = input("\n按回车键拍照识别（输入 q 退出）: ").strip().lower()
        if cmd == "q":
            print("👋 再见！")
            break
        try:
            img = take_photo()
            desc = identify_object(img)
            if desc:
                speak(desc)
        except Exception as e:
            print(f"⚠️ 出错: {e}")
