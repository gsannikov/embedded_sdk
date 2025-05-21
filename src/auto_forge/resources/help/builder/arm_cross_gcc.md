# ARM Cross GCC Toolchain Guide

This document provides a reference for setting up and troubleshooting ARM cross-compilers for:

- AArch64 (e.g. Raspberry Pi 3/4/5, 64-bit Linux)
- ARM Cortex-M (e.g. M4, M7, bare metal)
- Other common targets using GCC-based toolchains

---

## 📁 Toolchain Types

| Target         | Toolchain Prefix       | Notes                                 |
|----------------|------------------------|---------------------------------------|
| AArch64 Linux  | `aarch64-linux-gnu-`   | Full Linux toolchain for 64-bit ARM   |
| ARMv7 Linux    | `arm-linux-gnueabihf-` | 32-bit hard-float Linux (e.g. Pi 2/3) |
| ARM bare metal | `arm-none-eabi-`       | No OS, for Cortex-Mx / Cortex-Rx      |

---

## 📦 Installing Toolchains

### Fedora (limited coverage)

```
sudo dnf install gcc-aarch64-linux-gnu
sudo dnf install gcc-arm-linux-gnu
sudo dnf install arm-none-eabi-gcc-cs
```

> ⚠️ Fedora's cross toolchains may **not include** C++ headers, runtime libs, or sysroots.

---

## ✅ Recommended: Arm GNU Toolchain (prebuilt)

Official toolchains from Arm:

https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads

### AArch64 Linux Toolchain

```
cd ~/Downloads
wget https://developer.arm.com/-/media/Files/downloads/gnu/14.2.rel1/binrel/arm-gnu-toolchain-14.2.rel1-x86_64-aarch64-none-linux-gnu.tar.xz

sudo mkdir -p /opt/arm-gnu-toolchain
cd /opt/arm-gnu-toolchain
sudo tar -xf ~/Downloads/arm-gnu-toolchain-14.2.rel1-x86_64-aarch64-none-linux-gnu.tar.xz

sudo ln -sfn arm-gnu-toolchain-14.2.rel1-x86_64-aarch64-none-linux-gnu current
```

Add to your shell config:

```
export PATH=/opt/arm-gnu-toolchain/current/bin:$PATH
```

Verify:

```
aarch64-none-linux-gnu-gcc --version
```

---

### Cortex-M Bare Metal (M4, M7, etc.)

```
wget https://developer.arm.com/-/media/Files/downloads/gnu/14.2.rel1/binrel/arm-gnu-toolchain-14.2.rel1-x86_64-arm-none-eabi.tar.xz

sudo mkdir -p /opt/arm-gnu-toolchain
cd /opt/arm-gnu-toolchain
sudo tar -xf ~/Downloads/arm-gnu-toolchain-14.2.rel1-x86_64-arm-none-eabi.tar.xz

sudo ln -sfn arm-gnu-toolchain-14.2.rel1-x86_64-arm-none-eabi current-eabi
```

Set path:

```
export PATH=/opt/arm-gnu-toolchain/current-eabi/bin:$PATH
```

Then:

```
arm-none-eabi-gcc --version
```

---

## 🧪 Common Issues

### ❌ Cannot find `<algorithm>` / `<array>` / `<cmath>`

You're using a compiler without the **target C++ standard library headers**. Fix:

- Use Arm GNU prebuilt toolchain (includes libstdc++ and headers)
- If using Fedora's cross toolchain: manually install or point to a sysroot from a target image

---

### ❌ Linking errors: missing `crt0.o`, `libc.so`, etc.

You're likely cross-compiling for Linux but missing the **sysroot**.

Fix:

- Use prebuilt toolchain with built-in sysroot
- Or extract `/lib`, `/usr/lib`, `/usr/include` from your target system (e.g., Raspberry Pi) and pass
  `--sysroot=/path/to/sysroot`

---

## 🛠 CMake Toolchain File (example)

```cmake
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

set(CMAKE_C_COMPILER   aarch64-none-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-none-linux-gnu-g++)

set(CMAKE_FIND_ROOT_PATH /opt/arm-gnu-toolchain/current/aarch64-none-linux-gnu)

set(CMAKE_SYSROOT /opt/arm-gnu-toolchain/current/aarch64-none-linux-gnu)
```

---

## 📚 References

- https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads
- https://releases.linaro.org/components/toolchain/binaries/
- https://fedoraproject.org/wiki/Features/CrossCompilers

---

## 💡 Tips

- Always verify with `which <compiler>` and `--version`
- Don't mix `arm-linux-gnueabihf` with `arm-none-eabi` — one is for Linux, the other for bare metal
- Use `-mcpu=cortex-a72` or `-march=armv8-a` for tuning if targeting Raspberry Pi

---
