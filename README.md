# Raspberry Pi 5 大模型基础环境搭建日志

> 树莓派 5 部署大模型 —— 从硬件组装到视觉 / 语音链路的全流程踩坑记录。
> 本文档记录的是**第一天**的进度：硬件与系统底层环境搭建完成，摄像头与语音模块均已被系统识别，下一步进入业务逻辑落地。

---

## 一、今日完成进度

| 状态 | 事项 | 说明 |
| :---: | :--- | :--- |
| ☑ | 硬件组装 | 铝合金外壳 + 散热片 + 风扇 + 供电 |
| ☑ | 系统刷写 | Raspberry Pi OS 64-bit |
| ☑ | 无头模式（Headless） | 无需显示器，纯 SSH 连接 |
| ☑ | 系统换源 | 清华 TUNA 镜像源 |
| ☑ | USB 摄像头拍照测试 | `fswebcam` 抓拍成功，设备号 `/dev/video0` |
| ☑ | USB 语音模块 | 麦克风阵列录音与扬声器放音均正常，声卡 `card 2` |

---

## 二、硬件实拍

### 1. 整机外观（铝合金外壳 + 网线接入）

![树莓派 5 铝合金外壳外观，已接入黄色网线](image-and-video/raspberrypi-5.jpg)

- 铝合金外壳顶部开孔用于被动散热，配合内部风扇形成风道；
- 图中可见 USB-A 蓝色接口（USB 3.0）与黄色以太网线，本机网络走**有线连接**，比 Wi-Fi 更稳定，也避免了 `raspberrypi.local` 解析问题；
- 树莓派 5 的网口、USB 口布局与树莓派 4 一致，但**供电要求显著提高**（见下文踩坑）。

### 2. 无头模式（Headless）配置参考

![Headless 模式配置说明：创建 ssh 空文件与 userconf.txt](image-and-video/Solve-problem.jpg)

截图是排查 SSH 连接时查到的标准做法，核心两点：

1. 在 boot 分区根目录创建一个**名为 `ssh` 的空文件**（无扩展名），系统首次启动时才会开启 SSH 服务；
2. 可选但推荐：创建 `userconf.txt`，内容为 `username:encryptedpassword`，密码用 `openssl passwd -6` 生成，或直接在 Raspberry Pi Imager 的「高级选项」里预先配置用户名 / 密码 / Wi-Fi。

---

## 三、踩坑复盘（今日最耗时的部分）

今天大量时间消耗在**系统层面**的基础环境上，根本原因是新版硬件（树莓派 5）叠加最新系统（Debian 12/13）带来的一系列新变化。

### 坑 1：供电与散热的「假故障」

**现象**：系统启动时直接卡死，**绿灯常亮**，无任何输出。

**原因**：

- 使用了功率不足的普通充电头（5V < 3A）；
- 未接风扇，缺少主动散热。

**教训**：

> 树莓派 5 必须使用 **5V 5A（至少 5V 3A）的 PD 协议电源**；铝合金外壳 + 风扇的散热是**必选项**。
> 绿灯常亮基本可以直接判定为「系统没有真正启动」，优先怀疑电源和散热，而不是软件。

### 坑 2：系统刷写与磁盘认知偏差

**现象**：起初将系统刷入 USB 读卡器，并尝试通过 USB 接口启动，导致长期**绿灯常亮**。

**教训**：

> 树莓派 5 的标准启动方式是**把 MicroSD 卡插在主板底部的专用卡槽**。
> USB 读卡器 / U 盘 / NVMe 也可以作为系统盘，但**需要先写入引导程序（bootloader）并配置启动顺序**，不能直接插上就用。

### 坑 3：无头模式（Headless）网络连接

**现象**：

- `raspberrypi.local` 在 Windows 下解析失败；
- `arp -a` 找到多个 IP，无法判断哪个是树莓派；
- SSH 报 `Connection refused`。

**教训**：

> - `Connection refused` 通常意味着**系统根本没启动**（对应绿灯常亮），或者**使用了错误的用户名** —— 树莓派默认**没有 root**，必须用 `pi` 或刷写时自定义的用户名；
> - 不要迷信 `raspberrypi.local` / mDNS，**直接去路由器后台看 DHCP 客户端列表**拿 IP 最快；
> - `arp -a` 里的 IP 混杂了手机、电脑等设备，只有结合路由器后台才能确认。

### 坑 4：新版系统的换源与 Python 环境

**变化 1 —— 换源文件格式变了**：

旧版的 `raspi.list` 不再存在，被结构化的 **`raspi.sources`**（`.sources` 格式）取代。

**变化 2 —— Python 外部管理环境保护**：

Python 3.11+ 默认开启 `externally-managed-environment` 保护，直接 `pip3 install` 会直接报错。

**教训**：

> - 换源时要注意 `/etc/apt/sources.list.d/` 下现在同时存在 `debian.sources` 与 `raspi.sources`；
> - 安装 Python 依赖有两条路：**优先用 `apt` 装系统包**（`python3-xxx`），确需 pip 时再考虑虚拟环境（venv）或 `--break-system-packages`。

---

## 四、下阶段工作重点：视觉与语音

当前硬件状态：**摄像头和语音模块均已被系统底层识别**，接下来的核心工作是**跑通业务逻辑**。

### 📷 摄像头（视觉）

**当前状态**：`fswebcam` 可正常抓拍，设备号为 `/dev/video0`。

**下一步计划**：

1. **实时视频流**：使用 `mjpg-streamer` 或 `motion` 搭建视频流服务，方便在 Windows 电脑浏览器中实时查看画面，为后续视觉识别做准备；
2. **视觉大模型对接**：参照配套商家提供的 **LLaVA 多模态大模型**教程，配置 Python 脚本，将图像输入大模型进行识别。

### 🎤 语音识别

**当前状态**：声卡编号为 **card 2**（USB PnP Audio Device），录音和放音均正常。

**下一步计划**：

1. **环境配置（修复 pip 报错）**
   使用 apt 绕过系统保护安装依赖：

   ```bash
   sudo apt install python3-speechrecognition python3-pyaudio flac -y
   ```

2. **解决离线 / 在线识别瓶颈**

   > ⚠️ **风险提示**：测试脚本中的 `recognize_google` 在国内大概率会超时。

   - **行动方向 A（推荐）**：引入 **Vosk 离线识别引擎**，下载轻量级中文模型（约 40–50MB），实现**无需联网**的实时语音转文字（ASR）；
   - **行动方向 B**：注册**百度智能云**或**讯飞开放平台**，申请免费 API Key，替换为国内 API 调用。

3. **打通大模型对话链路**
   将 ASR（语音转文字）的输出作为 Prompt 发送给**本地 Ollama** 或联网大模型，再把大模型返回的文字通过 **TTS（文本转语音）** 输出到 `card 2` 扬声器播放。

   ```text
   麦克风(card 2) ──► ASR (Vosk/百度/讯飞) ──► Prompt ──► 大模型 (Ollama)
                                                                │
   扬声器(card 2) ◄──────────── TTS (文本转语音) ◄──────────────┘
   ```

---

## 五、常用命令备忘（Copy-Paste 备用）

### 1. 查看声卡设备与调试

```bash
arecord -l   # 查看录音设备（确认 card 2）
aplay -l     # 查看播放设备
alsamixer    # 按 F6 选择 USB PnP Audio Device 调节音量（按 M 解除静音）
```

### 2. 强制使用 USB 声卡录音与播放测试

```bash
arecord -D plughw:2,0 -d 5 -f cd test_voice.wav   # 录 5 秒
aplay -D plughw:2,0 test_voice.wav                # 播放录音
```

### 3. 摄像头抓拍测试

```bash
fswebcam -d /dev/video0 --skip 20 -r 1280x720 --no-banner test2.jpg
```

### 4. 将文件拉回 Windows 电脑（在 Windows PowerShell 中执行）

```powershell
scp pi@树莓派IP地址:~/test.jpg .
```

> 也可以直接在树莓派本地查看图片。

### 5. 系统状态自检（排查绿灯常亮 / 未启动时）

```bash
vcgencmd measure_temp     # 查看 SoC 温度
vcgencmd get_throttled    # 查看是否发生欠压降频（0x0 为正常）
free -h                   # 内存占用
df -h                     # 磁盘占用
```

---

## 六、仓库结构

```text
raspberrypi-5/
├── README.md                        # 本日志（部署过程 + 踩坑 + 命令备忘）
└── image-and-video/
    ├── raspberrypi-5.jpg            # 整机外观（铝合金外壳 + 网线）
    └── Solve-problem.jpg            # Headless 模式（ssh 文件 / userconf.txt）配置参考
```

---

## 七、后续 TODO

- [ ] 搭建 `mjpg-streamer` / `motion` 视频流服务，Windows 浏览器实时预览
- [ ] 按商家教程跑通 LLaVA 图像识别脚本
- [ ] apt 安装 `python3-speechrecognition python3-pyaudio flac`
- [ ] 部署 Vosk 中文离线模型，实现离线 ASR
- [ ] 接入 Ollama 本地大模型，打通「语音 → 大模型 → 语音」全链路
- [ ] 补充每日部署日志与实拍图
