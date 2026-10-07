# 手势控制风扇（树莓派端）

> 用 USB 摄像头识别 ✌️(Victory) 手势，经 MQTT 控制 STM32 风扇开 / 关。
> 本文说明树莓派系统需要装哪些库、我们踩过的坑（避坑指南），以及运行方式。
>
> ⚠️ 重要：本功能**只用了之前已有的代码，下位机（STM32 / ESP32）固件未做任何修改**。
> 树莓派只是往 MQTT 主题 pi/to/esp32 发指令而已。

---

## 一、整体链路

    USB 摄像头 ──▶ 树莓派(MediaPipe 手势识别) ──MQTT(pi/to/esp32)──▶ ESP32 ──UART──▶ STM32 风扇

- ✌️(Victory) => 发 FAN_ON
- 放下手      => 发 FAN_OFF
- 2 秒防抖；摄像头窗口内实时显示提示与状态

---

## 二、需要安装的库（Raspberry Pi 5 / Raspberry Pi OS 64-bit）

**1) 系统级**

    sudo apt-get update
    sudo apt-get install -y mosquitto mosquitto-clients fonts-dejavu-core
    sudo usermod -aG video $USER

（重新登录后 video 组权限才生效，否则打不开 /dev/video0）

**2) Python 包（建议在项目 venv 里）**

    cd ~/deepseek_project
    python3 -m venv venv && source venv/bin/activate
    pip install opencv-python mediapipe paho-mqtt

**3) 手势模型（约 8MB，需自行下载）**

    mkdir -p ~/deepseek_project/models
    cd ~/deepseek_project/models
    curl -L -o gesture_recognizer.task 'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task'

---

## 三、运行

    cd ~/deepseek_project
    source venv/bin/activate
    DISPLAY=:0 python3 gesture_fan_control.py

- 窗口显示在树莓派接的显示器上；终端每 2 秒打印一次「看到手势 / 风扇状态」
- 比 ✌️ 开风扇，放手关风扇，按 q 退出

---

## 四、避坑指南

**1. mediapipe 1.x 已删除 mp.solutions**
旧教程的 mp.solutions.hands 会直接报 AttributeError。新版必须改用 Tasks API：
mediapipe.tasks.python.vision.GestureRecognizer。它自带 Victory 手势，比手算关节角度更稳。

**2. 模型下载容易“残缺”**
Google 源在国内很慢，下载常中断；残缺的 zip 会导致 RuntimeError: Unable to open zip archive.
务必校验文件大小（应为 8373440 字节）并确认是合法 zip。网慢时可并行分块下载。

**3. OpenCV 刷屏 QFontDatabase: Cannot find font directory .../cv2/qt/fonts**
OpenCV 自带 Qt 缺字体目录。补上即可：

    mkdir -p venv/lib/python3.13/site-packages/cv2/qt/fonts
    cp /usr/share/fonts/truetype/dejavu/*.ttf venv/lib/python3.13/site-packages/cv2/qt/fonts/

**4. paho-mqtt 2.x 的弃用警告**
mqtt.Client(...) 要显式指定 CallbackAPIVersion，否则提示 Callback API version 1 已弃用。
本脚本用 CallbackAPIVersion.VERSION2。

**5. 摄像头被占用：Camera index out of range / isOpened: False**
多半是上一个进程没退干净，仍占着 /dev/video0。先 pkill -f gesture_fan_control.py 再重跑。

**6. SSH 下看不到画面**
cv2.imshow 需要显示环境。纯 SSH 会话请加 DISPLAY=:0（显示到树莓派接的显示器），或改用 VNC。

**7. 窗口必须给操作提示**
否则用户不知道要做什么。本脚本在画面里画了：当前手势、风扇状态，以及「比 V 开 / 放手关 / q 退出」。

---

## 五、与下位机的关系

**没有修改任何下位机代码。** STM32 / ESP32 用的还是前面的固件：

- ESP32 订阅 pi/to/esp32，把指令经 UART 转给 STM32
- STM32 收到 FAN_ON / FAN_OFF 控制风扇

树莓派这边只是「多了一种发指令的方式」——在网页看板、语音助手之外，又加了手势。
