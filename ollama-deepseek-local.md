# 树莓派 5 本地大模型部署：Ollama + DeepSeek-R1 实操指南

> **配套文档**：[README.md](README.md) 讲的是「系统 / 摄像头 / 声卡」基础环境；
> **本文讲的是下一层**——在树莓派 5 上把 **Ollama + DeepSeek-R1 小模型**真正跑起来，并对外提供 API。
>
> 目标读者：基础环境已跑通（能 SSH、`apt` 正常），现在想在本机跑一个能对话、能被 Python 调用的大模型的人。
> 本文不写流水账，写的是**可复现的路径 + 每个坑的现象、原因、解决**。

---

## 一、先看结论：这条路为什么需要「手动装 + 编译」

在树莓派 5（ARM64 / aarch64）上跑 Ollama，会连续遇到三件官方一键脚本**不会替你解决**的事：

1. **国内网络**：`curl -fsSL https://ollama.com/install.sh | sh` 极慢，甚至下到的是 HTML；
2. **安装包格式变了**：官方从 `.tgz` 改成了 **`.tar.zst`**，直接 `tar -xzf` 会报 `not in gzip format`；
3. **包内缺少 `llama-server`**：手动解压的包里**没有推理后端二进制**，模型能下载但一启动就报 `llama-server binary not found`——必须**自己从源码编译**。

所以最终路径是：**手动下载 → 解压 → 修权限 → 建 systemd 服务 → 拉模型 → 编译 `llama-server` → 重启 → 调用**。

**预计耗时**：下载 + 编译是主要开销。编译若没关掉多 CPU 变体，树莓派上可能跑 **2~4 小时**（见坑 7）。

---

## 二、整体成功路径（按顺序执行）

### 步骤 1：安装 Ollama 主程序（手动下载，绕开一键脚本）

```bash
# 下载 arm64 版本压缩包（.tar.zst 格式）
# 建议用 wget -O 明确指定文件名，避免把 HTML 错误页当成安装包存下来
wget -O ollama-linux-arm64.tar.zst <下载地址>

# 【重要】先验证文件类型，不要急着解压
file ollama-linux-arm64.tar.zst
# 期望输出包含：Zstandard compressed data
```

> 📌 **`file` 这一步不能省**。国内镜像 / 加速器经常返回一个 HTML 页面（比如限流页），文件名却是 `.tar.zst`。不验证的话，你会在后面的解压步骤看到一堆莫名其妙的报错。

```bash
sudo apt install -y zstd          # 先装 zstd，tar 才能识别 .zst
mkdir -p ~/ollama_temp
tar -I zstd -xf ollama-linux-arm64.tar.zst -C ~/ollama_temp
```

### 步骤 2：部署到系统目录并修复权限

```bash
# 把解压出来的内容复制到 /usr/local（bin 与 lib 两部分）
cd ~/ollama_temp
sudo cp bin/ollama /usr/local/bin/
sudo cp -r lib/ollama /usr/local/lib/

# 【重要】修复权限：sudo tar/cp 出来的文件属 root
sudo chmod 755 /usr/local/bin/ollama
sudo chmod -R a+rX /usr/local/lib/ollama
```

> ⚠️ **为什么必须做这一步**：`sudo` 解压 / 复制出来的文件**属主是 root**，普通用户执行 `ollama` 会直接 `Permission denied`。这两条命令是官方安装脚本内部做的事，手动安装就得自己补上。
> - `chmod 755 /usr/local/bin/ollama`：让所有用户可执行主程序；
> - `chmod -R a+rX /usr/local/lib/ollama`：`a+rX` 让所有用户可读，并**只对目录保留可进入权限**（`X` 不会给普通文件加执行位）。

**验证**：

```bash
which ollama && ollama --version
```

### 步骤 3：创建 systemd 服务，让它后台常驻

目标：让 `ollama serve` 开机自启、后台运行，并**监听 `0.0.0.0:11434`**（这样才能被局域网内其他设备访问）。

```ini
# /etc/systemd/system/ollama.service
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="OLLAMA_HOST=0.0.0.0:11434"

[Install]
WantedBy=default.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
```

**验证（两条都要过）**：

```bash
sudo journalctl -u ollama -n 50 --no-pager      # 看日志有无报错
curl http://localhost:11434                     # 期望返回：Ollama is running
```

> 💡 `OLLAMA_HOST=0.0.0.0:11434` 是**允许局域网访问**的关键。只写 `11434` 或 `127.0.0.1:11434` 时，只有本机能连——从 Windows 调用会一直失败，却看不出任何报错。

### 步骤 4：拉取模型

```bash
ollama run deepseek-r1:1.5b
```

**模型怎么选（这一步选错会浪费最多时间）**：

| 模型 | 体积 | 4GB 内存树莓派 5 | 8GB 内存树莓派 5 |
| :--- | :--- | :--- | :--- |
| **`deepseek-r1:1.5b`** | 约 1.1 GB | ✅ **首选，可跑** | ✅ 首选 |
| `deepseek-r1:7b` | 约 4.7 GB | ❌ 内存不足 | ⚠️ 可跑但**非常慢** |
| `qwen3:1.7b` | 约 1.4 GB | ✅ 可用 | ✅ 可用 |
| `Phi-3-Mini` | 约 2.3 GB | ⚠️ 吃紧 | ✅ 可用 |

> 📌 **树莓派上不要一开始就上 7B**。1.5B 是「能实际用起来」的甜点：响应快、内存够、还能和语音链路串起来。

此时**大概率会遇到下一个错误**（这也是本文的核心）：

```text
llama-server binary not found
```

模型能下载，但**推理后端不存在**——进入步骤 5。

### 步骤 5：从源码编译 `llama-server`（关键一步）

手动安装的包里缺 `llama-server`，需要从 Ollama 源码编译它：

```bash
cd ~/ollama

cmake -S llama/server \
  -DGGML_CPU_ALL_VARIANTS=OFF \
  -DGGML_NATIVE=ON \
  -DGGML_BACKEND_DL=OFF \
  -DLLAMA_SUBPROCESS=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -B build/llama-server-cpu

cmake --build build/llama-server-cpu -j$(nproc)
```

**每个参数为什么必须有（理解了才不会漏）**：

| 参数 | 作用 | 不写的后果 |
| :--- | :--- | :--- |
| `-DGGML_CPU_ALL_VARIANTS=OFF` | 只为当前 CPU 编译一份 | ⚠️ 为**多种 ARM CPU 变体各编一遍**，可能跑 **2~4 小时** |
| `-DGGML_NATIVE=ON` | 按本机 CPU 特性优化 | 失去 SIMD 等加速，推理更慢 |
| `-DGGML_BACKEND_DL=OFF` | 不做后端动态加载 | 引入不必要的动态库依赖 |
| `-DLLAMA_SUBPROCESS=ON` | **开启子进程支持** | ❌ 启动报 `subprocess is not enabled on this build`，无法进入 Router 模式 |
| `-DCMAKE_BUILD_TYPE=Release` | 优化构建 | Debug 构建慢得多 |
| `-B build/llama-server-cpu` | **直接指定构建目录** | 依赖 preset 名字，容易报 `No such build preset` |

> 📌 **不要依赖 `--preset`**。`cmake -S llama/server --preset cpu` 这类写法经常报 `No such build preset "cpu"`，preset 名字随版本变动。**直接 `-B build/llama-server-cpu` 指定目录最稳**。

**编译太慢 / 内存不足（OOM）**：

```bash
cmake --build build/llama-server-cpu -j$(nproc)   # 跑满所有核心（快，但吃内存）
cmake --build build/llama-server-cpu -j1          # 内存不足时降并行度
```

> 若 `-j$(nproc)` 中途 OOM，降到 `-j2` / `-j1`，或先临时加 swap（编译期内存峰值比运行期高得多）。

### 步骤 6：部署编译产物并重启服务

```bash
sudo cp ~/ollama/build/llama-server-cpu/bin/llama-server /usr/local/lib/ollama/
sudo systemctl restart ollama

ollama run deepseek-r1:1.5b
```

**验证**：模型能正常对话，且 API 可访问：

```bash
curl http://localhost:11434
```

🎉 到此，`http://localhost:11434` 已成为一个**标准的、可被程序调用的本地大模型服务**。

---

## 三、常见坑与解决方法（按现象查）

### 坑 1：国内下载慢 / 镜像失效

- **现象**：`curl -fsSL https://ollama.com/install.sh | sh` 下载极慢；或加速器返回的是 **HTML 页面**而非安装包。
- **原因**：官方 CDN 在境外；部分加速器（如 `ghproxy.cn`）已失效或限流，会返回 HTML。
- **解决**：
  1. **手动下载 `.tar.zst`**，下载后必须 `file` 验证是否为 `Zstandard compressed data`；
  2. 换用其他加速器：`gh-proxy.com`、`ghproxy.net`、`github.akams.cn`；
  3. **优先用 `wget -O 文件名`**，明确指定输出文件名，避免把 HTML 存成压缩包。

> 📌 多个加速器轮换 + 每次 `file` 验证，是这一步最省时间的组合。

### 坑 2：解压格式变了（`.tar.zst`）

- **现象**：`tar -xzf` 报 **`not in gzip format`**。
- **原因**：Ollama 官方安装包从 `.tgz` 改为 **`.tar.zst`**，`-z`（gzip）不认 zstd。
- **解决**：

  ```bash
  sudo apt install -y zstd
  tar -I zstd -xf ollama-linux-arm64.tar.zst -C 目标目录
  ```

> `-I zstd` 表示「用 zstd 作为过滤器」，这是新版 tar 推荐的写法。

### 坑 3：权限问题（Permission denied）

- **现象**：普通用户执行 `ollama` 报 **`Permission denied`**。
- **原因**：用 `sudo tar` / `sudo cp` 解压复制出的文件**属主是 root**，普通用户无执行 / 读权限。
- **解决**：

  ```bash
  sudo chmod 755 /usr/local/bin/ollama
  sudo chmod -R a+rX /usr/local/lib/ollama
  ```

### 坑 4：`llama-server` 缺失（**本文核心坑**）

- **现象**：模型能下载，但一运行就报 **`llama-server binary not found`**。
- **原因**：**手动解压的安装包里没有推理后端二进制** `llama-server`，Ollama 无法启动模型。官方一键安装脚本会补齐，手动安装则不会。
- **解决**：从源码编译（步骤 5）：

  ```bash
  cmake -S llama/server -DGGML_CPU_ALL_VARIANTS=OFF -DGGML_NATIVE=ON \
        -DGGML_BACKEND_DL=OFF -DLLAMA_SUBPROCESS=ON \
        -DCMAKE_BUILD_TYPE=Release -B build/llama-server-cpu
  cmake --build build/llama-server-cpu -j$(nproc)
  sudo cp ~/ollama/build/llama-server-cpu/bin/llama-server /usr/local/lib/ollama/
  ```

> ⚠️ 网上有教程写 `cmake -S llama/server --preset cpu`。**preset 名不一定存在**，直接指定 `-B build/llama-server-cpu` 更稳（见坑 5）。

### 坑 5：CMake preset 名字不对

- **现象**：`cmake --build --preset cpu` 报 **`No such build preset "cpu"`**。
- **原因**：preset 名称随 Ollama 版本变动，教程里的名字可能已失效。
- **解决**：不使用 preset，直接进构建目录构建：

  ```bash
  cd build/llama-server-cpu && cmake --build . -j$(nproc)
  ```

### 坑 6：编译时未开启子进程支持

- **现象**：`llama-server` 启动报 **`subprocess is not enabled on this build`**，无法进入 Router 模式。
- **原因**：配置时缺少 `-DLLAMA_SUBPROCESS=ON`。
- **解决**：**重新配置时加上该参数再编译**（注意是重新 configure，不是只重新 build）：

  ```bash
  cmake -S llama/server ... -DLLAMA_SUBPROCESS=ON -B build/llama-server-cpu
  cmake --build build/llama-server-cpu -j$(nproc)
  ```

> 📌 **改编译选项必须重新 configure**。只跑 `cmake --build` 会沿用旧的缓存配置，你会以为「改了没用」。

### 坑 7：编译太慢 / 内存不足

- **现象**：编译耗时数小时；或中途进程被杀（OOM）。
- **原因**：`GGML_CPU_ALL_VARIANTS=ON` 会**为多种 ARM CPU 变体各编译一遍**，树莓派上可能跑 **2~4 小时**；`-j$(nproc)` 并行度高，内存峰值大。
- **解决**：
  1. 关闭多变体：`-DGGML_CPU_ALL_VARIANTS=OFF -DGGML_NATIVE=ON`；
  2. 用 `-j$(nproc)` 跑满核心（快）；
  3. 若 OOM，降低并行度：`-j1`，或先加 swap。

### 坑 8：环境变量没生效（`DEEPSEEK_API_KEY`）

- **现象**：Python 脚本读不到 `DEEPSEEK_API_KEY`，报 **`ValueError`**。
- **原因**：变量**没有 `export`**，只存在于当前 shell 或根本没设置；或用了 `sudo` 丢失环境。
- **解决**：

  ```bash
  export DEEPSEEK_API_KEY="sk-..."
  echo $DEEPSEEK_API_KEY        # 当前终端验证
  ```

  - **不要用 `sudo python`**——`sudo` 会重置环境变量，除非用 `sudo -E`；
  - **更安全的做法**：用 `python-dotenv` + `.env` 文件（**并把 `.env` 放进 `.gitignore`**）。

### 坑 9：模型选择不当

- **现象**：树莓派上跑 `deepseek-r1:7b` 非常慢，甚至内存不足。
- **解决**：见步骤 4 的模型选择表 —— **首选 `deepseek-r1:1.5b`（约 1.1GB，4GB 内存可跑）**；8GB 可尝试 7B 但速度明显下降；其他轻量选择 `qwen3:1.7b`、`Phi-3-Mini`。

### 坑 10：systemd 服务没起 / 端口不对

- **现象**：服务启动失败，或局域网设备连不上 11434。
- **解决**：
  1. 用 `Environment="OLLAMA_HOST=0.0.0.0:11434"` **允许局域网访问**；
  2. 启动后验证：`curl http://localhost:11434` 应返回 **`Ollama is running`**；
  3. 查看日志：`sudo journalctl -u ollama -n 50 --no-pager`；
  4. 改完 service 文件记得 `sudo systemctl daemon-reload`。

---

## 四、API 调用方式

### 4.1 本地 Ollama API（推荐：免费、离线、局域网可用）

```python
import requests

r = requests.post("http://localhost:11434/api/chat", json={
    "model": "deepseek-r1:1.5b",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": False
})
print(r.json()["message"]["content"])
```

**要点**：

- 端口默认 `11434`，**无需 API Key**（默认仅局域网可见，不要把端口直接暴露到公网）；
- `"stream": False` 便于直接取完整结果；做流式输出（打字机效果）时改成 `True` 并逐行读取；
- 从 **Windows / 其他设备**调用时，把 `localhost` 换成树莓派 IP，例如 `http://192.168.1.100:11434`，并确认服务端 `OLLAMA_HOST` 已设为 `0.0.0.0:11434`（坑 10）。**首次调用会较慢，因为模型需要加载进内存。**

### 4.2 官方 DeepSeek API（云端，作为能力补充）

```python
from openai import OpenAI

client = OpenAI(api_key="sk-...", base_url="https://api.deepseek.com")
resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "你好"}]
)
print(resp.choices[0].message.content)
```

**什么时候用云端而不是本地**：

| 场景 | 建议 |
| :--- | :--- |
| 离线、隐私数据、无网络 | ✅ 本地 Ollama（1.5B） |
| 需要高质量长回答、复杂推理 | ✅ 云端 API（`deepseek-chat`） |
| 语音对话链路 | 本地做快速响应，复杂问题再转云端 |

> 📌 **建议把两者写成同一个接口**：封装一个 `chat(prompt)` 函数，内部按「本地优先、失败或超时转云端」路由。这样上层语音 / 视觉代码不用关心底层用哪个模型。

---

## 五、命令速查（Copy-Paste）

### 安装与部署

```bash
wget -O ollama-linux-arm64.tar.zst <下载地址>
file ollama-linux-arm64.tar.zst                  # 必须为 Zstandard compressed data
sudo apt install -y zstd
tar -I zstd -xf ollama-linux-arm64.tar.zst -C ~/ollama_temp

sudo cp ~/ollama_temp/bin/ollama /usr/local/bin/
sudo cp -r ~/ollama_temp/lib/ollama /usr/local/lib/
sudo chmod 755 /usr/local/bin/ollama
sudo chmod -R a+rX /usr/local/lib/ollama
```

### 编译 llama-server

```bash
cd ~/ollama
cmake -S llama/server \
  -DGGML_CPU_ALL_VARIANTS=OFF -DGGML_NATIVE=ON \
  -DGGML_BACKEND_DL=OFF -DLLAMA_SUBPROCESS=ON \
  -DCMAKE_BUILD_TYPE=Release -B build/llama-server-cpu
cmake --build build/llama-server-cpu -j$(nproc)
sudo cp build/llama-server-cpu/bin/llama-server /usr/local/lib/ollama/
```

### 服务管理

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
sudo systemctl restart ollama
sudo systemctl status ollama
sudo journalctl -u ollama -n 50 --no-pager
curl http://localhost:11434                      # 期望：Ollama is running
```

### 模型操作

```bash
ollama run deepseek-r1:1.5b                      # 运行 / 首次自动拉取
ollama list                                      # 已下载模型
ollama ps                                        # 当前加载的模型
ollama rm <模型名>                                # 删除模型释放空间
```

### 环境变量

```bash
export DEEPSEEK_API_KEY="sk-..."
echo $DEEPSEEK_API_KEY
```

---

## 六、附录

### 附录 A：错误信息 → 章节速查

| 错误 / 现象 | 原因 | 章节 |
| :--- | :--- | :--- |
| `not in gzip format` | 安装包是 `.tar.zst`，用了 `-z` 解压 | 坑 2 |
| `Permission denied`（执行 ollama） | 文件属 root | 坑 3 |
| `llama-server binary not found` | 包内缺推理后端，需源码编译 | 坑 4 / 步骤 5 |
| `No such build preset "cpu"` | preset 名不存在 | 坑 5 |
| `subprocess is not enabled on this build` | 缺 `-DLLAMA_SUBPROCESS=ON` | 坑 6 |
| `ValueError`（读不到 API Key） | 未 `export` / 用了 `sudo` | 坑 8 |
| 局域网连不上 11434 | `OLLAMA_HOST` 未设 `0.0.0.0` | 坑 10 |
| 编译超过 2 小时 | `GGML_CPU_ALL_VARIANTS` 未关 | 坑 7 |
| 服务无响应 / 端口异常 | systemd 配置或未 reload | 步骤 3 / 坑 10 |

### 附录 B：关键参数速查

| 参数 / 变量 | 值 | 作用 |
| :--- | :--- | :--- |
| `OLLAMA_HOST` | `0.0.0.0:11434` | 允许局域网访问 API |
| `-DGGML_CPU_ALL_VARIANTS` | `OFF` | 避免为多种 CPU 变体重复编译（省 2~4 小时） |
| `-DGGML_NATIVE` | `ON` | 针对本机 CPU 优化 |
| `-DGGML_BACKEND_DL` | `OFF` | 关闭后端动态加载 |
| `-DLLAMA_SUBPROCESS` | `ON` | 开启子进程 / Router 模式支持 |
| `-DCMAKE_BUILD_TYPE` | `Release` | 优化构建 |
| API 端口 | `11434` | Ollama 默认 HTTP API 端口 |

---

## 七、复现清单（Checklist）

- [ ] 已用 `wget -O` 手动下载 `.tar.zst`，且 `file` 验证为 **Zstandard compressed data**
- [ ] 已 `apt install -y zstd`，用 `tar -I zstd -xf` 成功解压
- [ ] 主程序已复制到 `/usr/local/bin`，并执行 `chmod 755`
- [ ] 库目录已执行 `chmod -R a+rX /usr/local/lib/ollama`
- [ ] systemd 服务已创建，`Environment="OLLAMA_HOST=0.0.0.0:11434"`
- [ ] `curl http://localhost:11434` 返回 **Ollama is running**
- [ ] 已拉取 **`deepseek-r1:1.5b`**（约 1.1GB），未盲目上 7B
- [ ] 已用 `-DGGML_CPU_ALL_VARIANTS=OFF -DGGML_NATIVE=ON` 编译 `llama-server`
- [ ] 编译参数包含 **`-DLLAMA_SUBPROCESS=ON`**
- [ ] `llama-server` 已复制到 `/usr/local/lib/ollama/` 并 `restart ollama`
- [ ] `ollama run deepseek-r1:1.5b` 能正常对话
- [ ] Python 脚本能通过 `http://localhost:11434/api/chat` 拿到回答
- [ ] 若用云端 API，`DEEPSEEK_API_KEY` 已 `export` 并 `echo` 验证（或改用 `.env`）

---

## 八、参考资源

- [Ollama 官网](https://ollama.com/) ｜ [Ollama GitHub Releases](https://github.com/ollama/ollama/releases)（下载 `.tar.zst`）
- [DeepSeek 开放平台](https://platform.deepseek.com/)（云端 API）
- [DeepSeek-R1 模型说明](https://ollama.com/library/deepseek-r1)
- 基础环境（系统刷写 / SSH / 摄像头 / 声卡）→ 见本仓库 [README.md](README.md)
