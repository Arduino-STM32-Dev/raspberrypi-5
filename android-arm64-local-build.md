# 在 ARM64 树莓派上本地构建 Android APK（参考指南）

> 目标：在 aarch64 Linux（树莓派 5 / ARM 云主机）上**本机**构建 Android debug APK，
> 不使用云端 CI，且**全程免 sudo**（所有组件装在用户空间）。
>
> 核心难点：Google 发布的 aapt2 / aidl / zipalign 都是 **x86-64**，在 aarch64 上会报
> Exec format error。解决办法：改用 **Debian 的原生 aarch64 build-tools**，并让 Gradle 通过
> android.aapt2FromMavenOverride 指向它。

---

## 一、组件与镜像来源

| 组件 | 来源 | 备注 |
| :--- | :--- | :--- |
| aapt2 等 (aarch64) | Debian 包 android-libaapt / android-sdk-build-tools | 国内镜像快，免 sudo 取用 |
| JDK 21 | 清华 TUNA 镜像的 Temurin | 必须用自包含版；Debian 拆包 JDK 不可靠 |
| Gradle 8.13 | 腾讯云镜像 | Debian 的 gradle 只有 4.4，太旧 |
| android.jar (API 34) | 腾讯云 AndroidSDK 镜像 | 平台包，与架构无关 |
| AGP / Kotlin / androidx | 阿里云 Maven 镜像 | 首次构建会拉 600M+ |

---

## 二、搭建步骤（全部用户空间）

**1) 取 Debian 的 aarch64 构建工具**（apt-get download + dpkg-deb -x，免 root）

    mkdir -p ~/opt/android-sdk ~/opt/aapt2
    cd /tmp && mkdir -p dl && cd dl
    apt-get download android-sdk-build-tools android-libaapt \
        aapt aidl zipalign apksigner split-select \
        android-libandroidfw android-libbase android-libutils android-liblog \
        android-libziparchive android-libcutils libzopfli1
    mkdir -p x && for f in *.deb; do dpkg-deb -x "$f" x; done
    mkdir -p ~/opt/android-sdk/libs
    find x -name '*.so*' -exec cp -a -n {} ~/opt/android-sdk/libs/ \;
    cp -a x/usr/lib/android-sdk/build-tools/debian/. ~/opt/aapt2/

aapt2 运行依赖动态库，必须设置 LD_LIBRARY_PATH：

    export LD_LIBRARY_PATH=$HOME/opt/android-sdk/libs
    ~/opt/aapt2/aapt2 version

**2) 装 JDK（自包含 Temurin）**

    cd ~/opt
    curl -sL -o temurin.tar.gz \
      'https://mirrors.tuna.tsinghua.edu.cn/Adoptium/21/jdk/aarch64/linux/OpenJDK21U-jdk_aarch64_linux_hotspot_21.0.12.1_1.tar.gz'
    mkdir jdk && tar xzf temurin.tar.gz -C jdk --strip-components=1
    export JAVA_HOME=$HOME/opt/jdk

**3) 装 Gradle**

    cd ~/opt
    curl -sL -o gradle.zip https://mirrors.cloud.tencent.com/gradle/gradle-8.13-bin.zip
    unzip -q gradle.zip

**4) 取 API 34 平台（android.jar）**

    mkdir -p ~/opt/android-sdk/platforms
    curl -sL -o p34.zip https://mirrors.cloud.tencent.com/AndroidSDK/platform-34-ext7_r03.zip
    unzip -q p34.zip -d /tmp/p34
    cp -r /tmp/p34/android-34 ~/opt/android-sdk/platforms/

**5) 造 build-tools 目录 + 接受许可**

    BT=~/opt/android-sdk/build-tools
    mkdir -p $BT/35.0.0
    cp -a ~/opt/aapt2/. $BT/35.0.0/
    printf 'Pkg.Revision=35.0.0\n' > $BT/35.0.0/source.properties
    mkdir -p ~/opt/android-sdk/licenses
    printf '\n8933bad161af4178b1185d1a37fbf41ea5269c55\nd56f5187479451eabf01fb78af6dfcb131a6481e\n24333f8a63b6825ea9c5514f83c2829b004d1fee\n' > ~/opt/android-sdk/licenses/android-sdk-license

**6) 工程侧配置**

local.properties：

    sdk.dir=/home/pi/opt/android-sdk

gradle.properties：

    android.aapt2FromMavenOverride=/home/pi/opt/aapt2/aapt2

settings.gradle.kts：把阿里云镜像放到 google()/mavenCentral() 之前

    pluginManagement {
        repositories {
            maven { url = uri("https://maven.aliyun.com/repository/gradle-plugin") }
            maven { url = uri("https://maven.aliyun.com/repository/google") }
            maven { url = uri("https://maven.aliyun.com/repository/public") }
            google(); mavenCentral(); gradlePluginPortal()
        }
    }
    dependencyResolutionManagement {
        repositories {
            maven { url = uri("https://maven.aliyun.com/repository/google") }
            maven { url = uri("https://maven.aliyun.com/repository/public") }
            google(); mavenCentral()
        }
    }

**7) 构建**

    export JAVA_HOME=$HOME/opt/jdk
    export PATH=$JAVA_HOME/bin:$HOME/opt/gradle-8.13/bin:$PATH
    export ANDROID_HOME=$HOME/opt/android-sdk
    export LD_LIBRARY_PATH=$HOME/opt/android-sdk/libs
    cd <你的工程>
    gradle --no-daemon clean assembleDebug
    # 产物： app/build/outputs/apk/debug/app-debug.apk

---

## 三、避坑指南

1. **aapt2: Exec format error** —— Google 的 aapt2 是 x86-64；必须用 Debian aarch64 版 + override。
2. **aapt2 缺 libaapt2.so.0 / libandroidfw.so.0 等** —— 把相关 .so 收齐，导出 LD_LIBRARY_PATH。
3. **别用 Debian 拆包出来的 JDK** —— 它的 java.security 被放到 /etc/java-21-openjdk/、cacerts 也缺失，
   会导致 keytool 抛 JceSecurity.<clinit>、Gradle 抛 SSL ExceptionInInitializerError。直接用自包含 Temurin。
4. **Debian 的 gradle 只有 4.4**，远低于 AGP 8.x 要求 —— 用腾讯镜像的 Gradle 8.13。
5. **AGP 要 build-tools;35.0.0 且提示许可未接受** —— 造该目录 + 写 licenses 文件。
6. **build-tools 目录 id 冲突** —— 多个目录携带同一个 Pkg.Revision 会让 AGP 混乱（会去装 35.0.0-2）。
   只保留一个版本目录，aapt2 单独放 ~/opt/aapt2 供 override。
7. **国内下载慢** —— Maven 用阿里云、Gradle/平台包用腾讯云、JDK 用 TUNA、Debian 用清华源。
8. **首次构建**要下 600M+ 依赖（约 2 分钟）；之后增量构建很快。
9. 用 gradle --no-daemon 避免残留守护进程；判定成功要看输出 “N actionable tasks: M executed”（M>0），
   若全是 UP-TO-DATE 说明没有真正编译，不算成功。

---

## 四、一键构建脚本（放工程根目录）

    #!/usr/bin/env bash
    set -e
    export JAVA_HOME=$HOME/opt/jdk
    export PATH=$JAVA_HOME/bin:$HOME/opt/gradle-8.13/bin:$PATH
    export ANDROID_HOME=$HOME/opt/android-sdk
    export ANDROID_SDK_ROOT=$HOME/opt/android-sdk
    export LD_LIBRARY_PATH=$HOME/opt/android-sdk/libs
    cd "$(dirname "$0")"
    gradle --no-daemon clean assembleDebug
    ls -l app/build/outputs/apk/debug/app-debug.apk
