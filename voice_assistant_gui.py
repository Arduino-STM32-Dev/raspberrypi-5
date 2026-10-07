#!/usr/bin/env python3
"""语音助手桌面 GUI（PyQt6）。

包装同目录下的 voice_assistant.py，把
  录音 -> 语音识别 -> 对话/查询 -> 本地 TTS 播报
做成图形界面：
  - 「开始说话」按钮走语音输入；
  - 文本框可直接打字提问（问温度/湿度、开关风扇）；
  - 对话日志 + 状态栏实时显示进度；
  - 「停止」按钮可中断录音/播报。
"""

import os
import sys
from html import escape

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

VA = None  # 后台导入完成后指向 voice_assistant 模块


class ModuleLoader(QThread):
    """后台导入 voice_assistant（顶层会加载本地 TTS 模型，耗时较长）。"""

    loaded = pyqtSignal()
    failed = pyqtSignal(str)

    def run(self):
        global VA
        try:
            import voice_assistant as va
            VA = va
            self.loaded.emit()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class AssistantWorker(QThread):
    """在独立线程中跑 录音/识别/对话/播报，避免卡住界面。"""

    status = pyqtSignal(str)
    user_text = pyqtSignal(str)
    reply_text = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, mode, text="", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.text = text
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        os.system("pkill -f aplay 2>/dev/null")
        os.system("pkill -f arecord 2>/dev/null")

    def run(self):
        try:
            if self.mode == "voice":
                self.status.emit("🎙️ 正在录音（5 秒）...")
                audio_path = VA.record_audio(duration=5)
                if self._cancelled:
                    self.finished.emit(False, "已取消")
                    return
                if not audio_path:
                    self.finished.emit(False, "录音失败，请检查麦克风")
                    return

                self.status.emit("📝 正在识别语音...")
                user_text = VA.speech_to_text(audio_path)
                if self._cancelled:
                    self.finished.emit(False, "已取消")
                    return
                if not user_text:
                    self.finished.emit(False, "语音识别失败")
                    return
                self.user_text.emit(user_text)
            else:
                user_text = self.text.strip()
                if not user_text:
                    self.finished.emit(False, "请输入内容")
                    return
                self.user_text.emit(user_text)

            self.status.emit("🧠 正在处理...")
            reply = VA.answer(user_text)
            if self._cancelled:
                self.finished.emit(False, "已取消")
                return
            if not reply:
                self.finished.emit(False, "处理失败")
                return
            self.reply_text.emit(reply)

            self.status.emit("🔊 正在播报...")
            VA.speak(reply)

            self.finished.emit(True, "")
        except Exception as e:  # noqa: BLE001
            self.finished.emit(False, f"出错: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("语音助手")
        self.resize(560, 640)

        self.worker = None
        self.ready = False

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("对话记录会显示在这里...")

        self.status_label = QLabel("⏳ 正在加载模型，请稍候...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #555; padding: 6px;")

        self.voice_btn = QPushButton("🎙️ 开始说话")
        self.voice_btn.setMinimumHeight(48)
        self.voice_btn.setEnabled(False)
        self.voice_btn.clicked.connect(self.start_voice)

        self.stop_btn = QPushButton("🛑 停止")
        self.stop_btn.setMinimumHeight(48)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_current)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.voice_btn, 3)
        btn_row.addWidget(self.stop_btn, 1)

        self.input = QLineEdit()
        self.input.setPlaceholderText("也可以直接输入文字，例如：现在温度多少 / 开风扇")
        self.input.returnPressed.connect(self.send_text)
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self.send_text)
        self.send_btn.setEnabled(False)

        input_row = QHBoxLayout()
        input_row.addWidget(self.input, 3)
        input_row.addWidget(self.send_btn, 1)

        layout = QVBoxLayout()
        layout.addWidget(self.log, 1)
        layout.addWidget(self.status_label)
        layout.addLayout(btn_row)
        layout.addLayout(input_row)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.loader = ModuleLoader()
        self.loader.loaded.connect(self._on_loaded)
        self.loader.failed.connect(self._on_load_failed)
        self.loader.start()

    def _on_loaded(self):
        self.ready = True
        self.voice_btn.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.status_label.setText("✅ 就绪：点击「开始说话」或直接输入文字")
        self._append_log("系统", "语音助手已就绪")

    def _on_load_failed(self, err):
        self.status_label.setText(f"❌ 初始化失败: {err}")
        self._append_log("系统", f"初始化失败: {err}")

    def _append_log(self, who, text):
        self.log.append(f"<b>{escape(who)}:</b> {escape(text)}")
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _set_busy(self, busy):
        self.voice_btn.setEnabled(self.ready and not busy)
        self.send_btn.setEnabled(self.ready and not busy)
        self.input.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def _connect_worker(self, w):
        w.status.connect(self.status_label.setText)
        w.user_text.connect(lambda t: self._append_log("👤 你", t))
        w.reply_text.connect(lambda t: self._append_log("🤖 助手", t))
        w.finished.connect(self._on_finished)

    def _on_finished(self, ok, msg):
        self._set_busy(False)
        self.status_label.setText("✅ 完成" if ok else f"⚠️ {msg}")

    def start_voice(self):
        if self.worker and self.worker.isRunning():
            return
        self._set_busy(True)
        self.worker = AssistantWorker("voice")
        self._connect_worker(self.worker)
        self.worker.start()

    def send_text(self):
        text = self.input.text()
        if not text.strip():
            return
        if self.worker and self.worker.isRunning():
            return
        self.input.clear()
        self._set_busy(True)
        self.worker = AssistantWorker("text", text)
        self._connect_worker(self.worker)
        self.worker.start()

    def stop_current(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
