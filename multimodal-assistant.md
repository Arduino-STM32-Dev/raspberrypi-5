# 树莓派多模态 AI 桌面助手（Raspberry Pi AI Assistant）

> 基于树莓派 + USB 摄像头 + USB 声卡，结合**云端视觉大模型**、**云端语音识别**与**本地离线 TTS**，
> 打造一个具备「**看、听、说、想**」能力的智能桌面机器人。
>
> 本文是项目的**阅读入口与设计说明**：讲清架构、拆解两个脚本、复盘全部踩坑、并给出面向
> **AI 辅助工业控制**的演进路线。
> **本文档会持续更新。**

---

## 一、项目定位

| 能力 | 实现方式 | 代码位置 |
| :--- | :--- | :--- |
| 👀 **看** | USB 摄像头拍照 → 云端 VLM 识别 | [vision_tts.py](vision_tts.py) |
| 👂 **听** | USB 麦克风录音 → 云端 ASR 转文字 | [voice_assistant.py](voice_assistant.py) |
| 🧠 **想** | 云端 LLM（DeepSeek）对话推理 | [voice_assistant.py](voice_assistant.py) |
| 🗣️ **说** | **本地离线** TTS 合成语音播报 | 两者均有 |

### 核心特点

- **📸 视觉识别与播报**：按回车 → USB 摄像头拍照 → 云端 VLM（Qwen-VL）识别物体 → 语音播报。
- **🎙️ 语音对话助手**：按回车 → USB 麦克风录音 → 云端 ASR（`qwen3-asr-flash`）识别 → DeepSeek 思考 → 语音播报。
- **🔌 完全本地化的 TTS**：使用 `sherpa-onnx` 加载 VITS 模型，**完全离线、0 延迟、不受网络波动影响**。
- **💰 极低成本**：ASR / VLM / LLM 均使用国内大厂 API（阿里云百炼 + DeepSeek），**按量付费**，日常使用成本极低。

### 项目整体数据流

```text
                     ┌──────────────── 视觉链路（vision_tts.py）─────────────┐
   USB 摄像头 ──fswebcam──► /tmp/cam.jpg ──base64──► 云端 VLM (qwen-vl-plus)
                                                              │
                                                              ▼
                                                       描述文字
                                                              │
                                                              ▼
                              ┌───────────────────────► TTS 语音播报
                              │
   USB 麦克风 ──arecord──► /tmp/input.wav ──file://──► 云端 ASR (qwen3-asr-flash)
                              │                              │
                              │                              ▼
                              │                          用户文字
                              │                              │
                              │                              ▼
                              └────────────────────── 云端 LLM (deepseek-flash)
                                                             │
                                                             ▼
                                                        回答文字 ──► TTS 语音播报
                     └──────────────── 语音链路（voice_assistant.py）──────────┘
```

> **一句话理解**：树莓派只负责「**采音、拍照、放音**」这三件轻量的事，
> 真正吃算力的「识别与推理」全部交给云端 API——这是本方案能在树莓派上跑出
> 秒级响应的**根本原因**（见坑 4.1）。

---

## 二、硬件与系统要求

| 项目 | 要求 |
| :--- | :--- |
| **主板** | 树莓派 4B / 5（**推荐 4GB 内存及以上**） |
| **外设** | USB 摄像头、USB 麦克风 / 声卡、扬声器 |
| **系统** | Raspberry Pi OS (64-bit) / Debian Bookworm 及以上 |
| **网络** | **国内网络环境即可**（依赖国内 API 节点与 GitHub 镜像） |

> 📌 **强烈建议把麦克风和扬声器接在同一个 USB 声卡上**——这样录音和播放共用同一个 card 号，
> 配置最简单，也不会出现「采样率不一致 / 播放设备找不到」的连锁问题（见坑 3.1）。

硬件实物可参考基础篇的实拍图：[recorder-and-camera.jpg](image-and-video/recorder-and-camera.jpg)（左侧语音模块、右侧摄像头模块）。

---

## 三、快速开始

### 3.1 系统依赖安装

```bash
sudo apt update
sudo apt install -y fswebcam mpg123 alsa-utils zstd git cmake g++ gcc
```

### 3.2 Python 环境（**必须用虚拟环境**）

```bash
python3 -m venv venv
source venv/bin/activate
pip install dashscope sherpa-onnx soundfile openai numpy
```

> ⚠️ **不要用 `--break-system-packages`**。Debian 12+ 受 PEP 668 保护，正确做法是
> 创建虚拟环境再安装（见坑 4.2）。

### 3.3 下载本地 TTS 模型

由于 GitHub 下载限制，`sherpa-onnx-vits-zh-ll` 模型需要**手动下载**：

```bash
# 使用镜像加速下载
wget https://gh-proxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/sherpa-onnx-vits-zh-ll.tar.bz2

# 【务必验证】确认下载到的是压缩包而不是 HTML 错误页
file sherpa-onnx-vits-zh-ll.tar.bz2

tar xvf sherpa-onnx-vits-zh-ll.tar.bz2
rm sherpa-onnx-vits-zh-ll.tar.bz2
```

### 3.4 配置 API Key

复制 `.env.example` 为 `.env`，并填入你的真实 Key：

```text
DASHSCOPE_API_KEY=sk-你的阿里云百炼API_KEY
DEEPSEEK_API_KEY=sk-你的DeepSeek_API_Key
```

> ⚠️ **安全提醒**：`.env` 已在 [.gitignore](.gitignore) 中被忽略，**请勿把真实 Key 提交到仓库**。
> 当前两个脚本的 Key 写在文件顶部的配置区（`DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` / `API_KEY`），
> 后续计划统一改为从 `.env` 读取（见第八章演进计划）。

### 3.5 运行

```bash
# 运行语音助手
python voice_assistant.py

# 运行视觉识别
python vision_tts.py
```

---

## 四、代码解析（先读结构，再改参数）

两个脚本的结构完全一致，都是「**配置区 → 初始化 → 功能函数 → 主循环**」。理解了这个骨架，
你就可以把它们组合、拆分或改造成自己的应用。

### 4.1 [voice_assistant.py](voice_assistant.py)：语音对话链路

**配置区（你唯一需要改的地方）**：

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `DASHSCOPE_API_KEY` | `""` | 阿里云百炼 Key（**语音识别用**） |
| `DEEPSEEK_API_KEY` | `""` | DeepSeek 官方 Key（**对话思考用**） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek API 地址 |
| `MIC_DEVICE` | `plughw:2,0` | **必须改成你自己的 card 号** |
| `SPEAKER_DEVICE` | `plughw:2,0` | 同上，录音与播放可以是同一设备 |
| `TTS_MODEL_DIR` | `/home/pi/deepseek_project/sherpa-onnx-vits-zh-ll` | 本地 TTS 模型目录 |

**四个环节的关键实现**：

| 环节 | 函数 | 关键点 |
| :--- | :--- | :--- |
| 1️⃣ 录音 | `record_audio(duration=5)` | `arecord -D plughw:2,0 -d 5 -f S16_LE -r 16000 -c 1` —— **16kHz 单声道**是 ASR 的标准输入格式 |
| 2️⃣ 识别 | `speech_to_text()` | 用 `file://` 前缀传**本地绝对路径**，模型选 `qwen3-asr-flash` |
| 3️⃣ 对话 | `chat_with_deepseek()` | 模型 `deepseek-flash`，系统提示词约束「**口语化、不超过两句话、无表情符号**」 |
| 4️⃣ 播报 | `speak()` | `tts.generate()` → 写 WAV → `aplay -D plughw:2,0` 播放 |

**三个值得学习的细节**：

1. **TTS 引擎常驻内存**：`OfflineTts` 在程序启动时只加载一次（`tts = sherpa_onnx.OfflineTts(tts_config)`）。
   如果把加载写进循环，每次播报都要等模型加载，体验会完全崩掉。
2. **系统提示词是为「语音播报」定制的**：`回答要口语化、简短直接，不超过两句话，不要包含表情符号和特殊符号，适合语音播报`。
   ——这是**语音助手和聊天机器人的本质区别**：回答要能被「听」懂，而不是被「读」懂。
3. **空返回有兜底**：`if not response.choices or not response.choices[0].message.content:` 时返回
   「抱歉，我好像走神了，请再说一遍。」而不是崩溃 —— 语音场景里，**一次失败不应该中断整个会话**。

**TTS 模型必须齐四个文件**（少一个就报错，见坑 3.2）：

| 文件 | 作用 |
| :--- | :--- |
| `model.onnx` | VITS 声学模型本体 |
| `tokens.txt` | 音素 / 字符表 |
| `lexicon.txt` | 词典（中文发音） |
| `phone.fst` | 文本正则化规则（数字、符号如何读） |

### 4.2 [vision_tts.py](vision_tts.py)：视觉识别链路

**配置区**：

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `API_KEY` | `""` | 阿里云百炼 Key（**记得替换**） |
| `BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | **OpenAI 兼容模式**端点 |
| `MODEL_NAME` | `qwen-vl-plus` | 若报模型不存在，改成 `qwen-vl-max-latest` |
| `IMAGE_PATH` | `/tmp/cam.jpg` | 拍照落盘路径 |

**三个环节**：

| 环节 | 函数 | 关键点 |
| :--- | :--- | :--- |
| 1️⃣ 拍照 | `take_photo()` | `fswebcam -r 1280x720 --no-banner`，失败直接抛 `RuntimeError` |
| 2️⃣ 识别 | `identify_object()` | 图片 **base64 编码**后以 `data:image/jpeg;base64,...` 形式塞进 `image_url` |
| 3️⃣ 播报 | `speak()` | 当前仍使用 `edge-tts` + `mpg123`，**尚未迁移到本地 sherpa-onnx**（见坑 3.3） |

**值得注意的设计**：这里用的是**阿里云百炼的 OpenAI 兼容端点**，
所以可以直接用 `openai` 库的 `client.chat.completions.create()` 调用 Qwen-VL，
**不需要额外学习一套 SDK**。这是多模型协作时非常实用的技巧——**用一套接口代码，切换不同厂商的模型**。

### 4.3 两个脚本的差异对照（可组合性）

| 维度 | [voice_assistant.py](voice_assistant.py) | [vision_tts.py](vision_tts.py) |
| :--- | :--- | :--- |
| 输入模态 | 音频（`arecord`） | 图像（`fswebcam`） |
| 云端调用方式 | `dashscope` 原生 SDK + `openai` SDK | 纯 `openai` SDK（兼容模式） |
| 使用的模型 | `qwen3-asr-flash` + `deepseek-flash` | `qwen-vl-plus` |
| TTS 实现 | ✅ `sherpa-onnx` 本地离线 | ⚠️ `edge-tts` 云端（待迁移） |
| 交互方式 | 回车触发一轮对话 | 回车触发一次识别 |

> 💡 **下一步的自然演进**：把两者的 `speak()` 统一成同一个本地 TTS 模块，
> 再把「拍照 / 录音」做成可被上层调度的能力，就能得到一个**多模态统一入口**的助手。

---

## 五、⚠️ 血泪避坑指南（极其重要）

我们在开发过程中踩了无数的坑，整理如下，希望能帮后来者节约时间。

### 5.1 树莓派音频设备之坑

#### 坑 1：麦克风无法录音

- **现象**：`arecord` 报 **`audio open error: Invalid argument`**。
- **原因**：没有指定正确的录音设备，或采样率 / 声道数与设备不兼容。
- **解决**：
  1. 先用 `arecord -l` 找到 **USB 麦克风的 card 号**；
  2. 在代码中**强制指定** `-D plughw:X,0`；
  3. 录音格式用 ASR 友好的 `-f S16_LE -r 16000 -c 1`（16kHz 单声道）。

#### 坑 2：播放无声或报错 524

- **现象**：`aplay` 报 **`Unknown error 524`**，或完全没有声音。
- **原因**：**树莓派默认输出到 HDMI**，USB 声卡不是默认播放设备。
- **解决**：
  1. 用 `aplay -l` 确认 USB 声卡的设备号；
  2. 播放时加 `-D plughw:X,0`；
  3. **推荐把麦克风和扬声器接在同一个 USB 声卡上**（本机为 `card 2`），录音播放共用同一个设备号。

> 📌 **通用心法**：树莓派上**任何音频命令都要显式指定 `-D plughw:X,0`**，
> 不要依赖默认设备。这是本项目里出现频率最高的一类问题。

### 5.2 国内网络之坑

#### 坑 3：GitHub 下载慢或报错

- **现象**：直接下载极其缓慢，或下到的是 HTML 页面。
- **解决**：
  1. **必须使用加速镜像**，如 `gh-proxy.com`、`ghproxy.net`；
  2. **下载大文件后务必用 `file` 命令验证文件类型**；
  3. ❌ **切忌把 HTML 错误页面当成模型包解压**——那会得到一堆看不懂的报错。

#### 坑 4：`pip` 安装报 `externally-managed-environment`

- **现象**：`pip install` 被拒绝安装。
- **解决**：
  1. ❌ **不要使用 `--break-system-packages`**（会污染系统 Python，埋下长期隐患）；
  2. ✅ **必须创建虚拟环境**：

     ```bash
     python3 -m venv venv
     source venv/bin/activate
     pip install ...
     ```

### 5.3 TTS 选型之坑

#### 坑 5：放弃 `edge-tts`

- **现象**：`edge-tts` 音色确实好，但它是**调用微软服务器**，国内环境极易报 **`NoAudioReceived`**。
- **结论**：**完全离线、稳定的 `sherpa-onnx` 才是桌面机器人的更优解**。
- **权衡**：牺牲一点音色，换来**离线可用 + 零网络依赖 + 可预期延迟**，对桌面机器人是划算的。

#### 坑 6：TTS 模型路径要求严苛

- **现象**：启动即报错，提示缺少文件。
- **原因**：本地 TTS **必须提供正确的四个文件**，**少一个都会报错**：
  `model.onnx`、`tokens.txt`、`lexicon.txt`、`phone.fst`。
- **解决**：解压模型包后 `ls` 确认四个文件齐全，且**路径要写绝对路径**。

### 5.4 API 选型之坑

#### 坑 7：不要让树莓派跑本地大模型

- **现象**：在树莓派上编译并运行 `deepseek-r1:1.5b` **极其耗费资源，且回答质量差**。
- **结论**：**果断切换为云端 API**（DeepSeek `deepseek-flash`），**秒级响应，树莓派毫无压力**。

#### 坑 8：ASR 模型必须支持本地文件

- **现象**：`fun-asr` 等**异步模型需要公网 URL**，树莓派直接传本地文件会报 **404**。
- **原因**：异步 ASR 的工作方式是「你提供可公网访问的 URL，服务端去拉取」，本地路径它访问不到。
- **解决**：改用 **`qwen3-asr-flash`**，它**完美支持本地文件直接上传**（用 `file://` + 绝对路径）。

---

## 六、关键决策记录（为什么这么选）

这一章记录了项目的**技术选型与理由**，方便后来者理解「为什么不用另一种方案」。

| 决策点 | 选择 | 放弃的方案 | **理由** |
| :--- | :--- | :--- | :--- |
| **LLM 推理位置** | **云端 API** | 树莓派本地 Ollama | 本地 1.5B **资源消耗大、质量差**；云端 `deepseek-flash` 秒级响应（坑 7） |
| **TTS 引擎** | **本地 `sherpa-onnx`** | `edge-tts` 云端 | 云端 TTS 国内易报 `NoAudioReceived`；本地**离线、0 延迟**（坑 5） |
| **ASR 接口** | **`qwen3-asr-flash`** | `fun-asr` | 前者**支持本地文件上传**，后者需要公网 URL，本地传文件报 404（坑 8） |
| **VLM 调用方式** | **OpenAI 兼容端点** | 各家原生 SDK | 一套 `openai` 代码即可切换模型厂商，**降低多模型协作成本** |
| **依赖安装方式** | **venv 虚拟环境** | `--break-system-packages` | 避免污染系统 Python，符合 PEP 668（坑 4） |
| **音频设备指定** | **显式 `plughw:X,0`** | 依赖系统默认设备 | 树莓派默认走 HDMI，不指定必然出问题（坑 1、2） |

> 📌 **最重要的一条设计哲学**：
> **把重计算交给云端，把实时性和稳定性留在本地。**
> 推理（LLM / VLM / ASR）放云端换取质量与速度；播报（TTS）留本地换取**离线可用与固定延迟**。
> 这个「**云脑 + 本地嘴**」的组合，是树莓派这类边缘设备做 AI 助手的实用解。

> ⚠️ **与本仓库另一篇文档的关系**：[ollama-deepseek-local.md](ollama-deepseek-local.md) 记录了
> **在树莓派上本地部署 Ollama + DeepSeek-R1** 的完整过程（含编译 `llama-server`）。
> 那是**学习与验证性质**的探索，证明了「能跑」；而本项目的结论是「**不建议用于实际助手**」（坑 7）。
> 两者并不矛盾——**先知道本地能做到什么程度，才能理性地决定哪里该用云端**。

---

## 七、代码现状与已知限制（诚实说明）

作为一份给他人阅读的文档，有必要说明**当前代码还不是最终形态**：

| 项 | 现状 | 影响 | 计划 |
| :--- | :--- | :--- | :--- |
| API Key 位置 | **硬编码在脚本顶部配置区** | 不便管理，易误提交 | 改为从 `.env` 读取（`python-dotenv`） |
| TTS 实现 | `voice_assistant.py` 已用本地 sherpa-onnx，**`vision_tts.py` 仍用 `edge-tts`** | 视觉链路仍受网络影响 | 抽出公共 `tts.py`，两条链路统一本地 TTS |
| 交互方式 | 固定 5 秒录音、回车触发 | 需要手动按键，不够自然 | 加 VAD（静音检测）自动断句、唤醒词唤醒 |
| 配置方式 | 需手动改脚本里的 card 号与路径 | 换机器要改代码 | 提取到 `.env` 或配置文件 |
| 错误处理 | 有基础兜底，但无重试 | 网络抖动会丢一轮对话 | 增加 API 重试与降级策略 |

> 这些限制**不影响当前功能可用**，但都是下一步要解决的真实问题。

---

## 八、后续演进路线（为 AI 辅助工业控制铺垫）

本项目的价值不只是「一个桌面玩具」，而是**验证了一条可复用的边缘 AI 感知链路**。
下面是从「桌面助手」走向「工业控制」的演进路径。

### 阶段一：打磨助手本身（近期）

- [ ] 把两个脚本的 **API Key 统一改为从 `.env` 读取**
- [ ] 抽出公共 **`tts.py`**，让 `vision_tts.py` 也用上本地离线 TTS
- [ ] 增加 **VAD 自动断句**，替代固定 5 秒录音
- [ ] 增加 **API 失败重试**与降级（云端失败时给出本地兜底话术）

### 阶段二：多模态融合

- [ ] **语音 + 视觉联动**：说「看看前面有什么」→ 自动拍照 → 识别 → 播报
- [ ] 增加**持续感知模式**：定时抓拍并判断是否有异常（为工业巡检铺垫）
- [ ] 接入**唤醒词**（如 `sherpa-onnx` 的 KWS 模型），实现免按键交互

### 阶段三：面向 AI 辅助工业控制（本文档的核心目的）

将现有的「感知 → 推理 → 播报」链路，扩展为「**感知 → 推理 → 播报 + 控制输出**」：

```text
  工业现场                          树莓派边缘节点                    执行
┌─────────────┐               ┌──────────────────────────┐      ┌──────────────┐
│ 摄像头 / 传感│──感知数据────►│ 1. 视觉识别 (VLM)         │      │ GPIO 继电器   │
│ 麦克风 / 声级│               │ 2. 语音指令 (ASR)         │─────►│ 串口 / Modbus │
│ 温湿度 / 开关│               │ 3. 决策推理 (LLM)         │      │ 告警 / 播报   │
└─────────────┘               │ 4. 安全校验 (硬约束)      │      │ 记录 / 上报   │
                              └──────────────────────────┘      └──────────────┘
```

**关键设计原则（工业场景下必须遵守）**：

1. **AI 只做「建议」，不做「唯一决策」**——控制指令必须经过**硬约束校验层**（阈值判断、白名单、状态机），
   绝不能把 GPIO 直接交给大模型的文本输出；
2. **断网必须可降级**——本地 TTS、本地规则引擎保留；云端 API 不可用时，退回**安全默认状态**而不是停摆；
3. **一切可追溯**——识别结果、模型输出、最终控制动作都要**落盘记录**，便于事后复盘与责任界定；
4. **实时性与非实时性分离**——紧急停车、限位保护等**实时回路走本地硬件**，大模型只处理非实时的分析与交互。

**复用现有成果的方式**：

| 现有能力 | 在工业场景中的复用 |
| :--- | :--- |
| `vision_tts.py` 的拍照 + VLM | 仪表读数、设备状态、异物检测的**视觉巡检** |
| `voice_assistant.py` 的 ASR | 现场人员**语音指令**（解放双手，适合戴手套作业） |
| 本地 `sherpa-onnx` TTS | **无网环境的语音告警**与操作提示 |
| DeepSeek 对话 | 异常现象→可能原因的**辅助诊断问答** |

> 📌 **为什么这套架构适合工业边缘**：它把「必须联网的重计算」隔离在云端，
> 把「必须实时可靠的部分」留在本地，**天然符合工业场景对可用性和可控性的要求**。
> 后面我们会沿着这个方向持续迭代，把这条链路真正接到控制输出上。

---

## 九、仓库文档地图

| 文档 | 内容 | 阶段 |
| :--- | :--- | :--- |
| [README.md](README.md) | 基础环境：系统刷写、Headless SSH、换源、摄像头、声卡、故障排查 | ① 把硬件跑通 |
| [ollama-deepseek-local.md](ollama-deepseek-local.md) | 本地大模型：Ollama 安装、编译 `llama-server`、DeepSeek-R1 部署与 API | ② 验证本地推理能力 |
| **multimodal-assistant.md**（本文） | 多模态助手：架构设计、代码解析、踩坑复盘、工业控制演进路线 | ③ 做出可用的应用 |
| [voice_assistant.py](voice_assistant.py) | 语音对话脚本（ASR + LLM + 本地 TTS） | 代码 |
| [vision_tts.py](vision_tts.py) | 视觉识别脚本（拍照 + VLM + 播报） | 代码 |

---

## 十、复现清单（Checklist）

- [ ] `sudo apt install -y fswebcam mpg123 alsa-utils zstd git cmake g++ gcc`
- [ ] `arecord -l` / `aplay -l` 确认 USB 声卡 card 号（本机为 **card 2**）
- [ ] `alsamixer`（按 F6 选 USB 声卡）确认**未静音**
- [ ] 已用 `python3 -m venv venv` 创建并激活虚拟环境（**未使用 `--break-system-packages`**）
- [ ] `pip install dashscope sherpa-onnx soundfile openai numpy`
- [ ] 已用镜像下载 TTS 模型，并 `file` 验证为**压缩包而非 HTML**
- [ ] TTS 模型目录四个文件齐全：`model.onnx`、`tokens.txt`、`lexicon.txt`、`phone.fst`
- [ ] 阿里云百炼 Key 与 DeepSeek Key 已配置，且 `.env` **不会被提交**
- [ ] `python voice_assistant.py` 能完成「录音 → 识别 → 回答 → 播报」完整一轮
- [ ] `python vision_tts.py` 能完成「拍照 → 识别 → 播报」完整一轮
- [ ] 已通读第五章避坑指南，知道出问题该查哪一条

---

## 十一、参考资源

- [阿里云百炼（DashScope）](https://bailian.console.aliyun.com/) —— ASR / VLM API
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) —— 本地离线 TTS / ASR 引擎
- [sherpa-onnx TTS 模型库](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models) —— `sherpa-onnx-vits-zh-ll`
- [DeepSeek 开放平台](https://platform.deepseek.com/) —— `deepseek-flash` 对话模型
- [Ollama](https://ollama.com/) —— 本地大模型（见 [ollama-deepseek-local.md](ollama-deepseek-local.md)）

---

> **持续更新中**：本项目面向 AI 辅助工业控制方向演进，欢迎关注后续提交。
