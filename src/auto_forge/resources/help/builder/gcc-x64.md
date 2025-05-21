# GCC x86_64 Native Toolchain Help

This guide explains how to install and troubleshoot the native x64 GCC toolchain on Linux systems.

---

## 🔧 What Is It?

This toolchain uses:

- `gcc` — the GNU C Compiler
- `g++` — the GNU C++ Compiler
- `make` — to drive multi-file builds (typically via a Makefile)

It compiles native executables for the same machine you're building on (x86_64 Linux).

---

## 🛠 Installation

### Fedora / RHEL / CentOS:

```bash
sudo dnf install gcc gcc-c++ make
```

### Ubuntu / Debian:

```bash
sudo apt update
sudo apt install build-essential
```

> `build-essential` includes `gcc`, `g++`, `make`, and common headers.

### Arch / Manjaro:

```bash
sudo pacman -S base-devel
```

---

## 🧪 Verify Installation

Check that tools are available:

```bash
which gcc
gcc --version

which g++
g++ --version

which make
make --version
```

Expected outputs should confirm version ≥ 10.0 and presence in `/usr/bin`.

---

## ✅ Compiler Configuration

Common Makefile variables:

```make
CC  := gcc
CXX := g++
LD  := ld

CFLAGS   := -Wall -Wextra -O2
CXXFLAGS := $(CFLAGS) -std=c++17
LDFLAGS  := -pthread
```

To compile:

```bash
make
```

---

## 🧰 Troubleshooting

### ❌ `gcc: command not found`

Install it via your package manager (see above).

### ❌ C++ headers like `<iostream>` or `<vector>` missing

Install `g++` / `gcc-c++`:

```bash
sudo dnf install gcc-c++
# or
sudo apt install g++
```

### ❌ `make` is missing

Install:

```bash
sudo dnf install make
# or
sudo apt install make
```

### ❌ `ld: cannot find crt1.o` or `libc.so`

This usually indicates you're missing the core system libraries. Try:

```bash
sudo dnf groupinstall "Development Tools" "Development Libraries"
# or
sudo apt install libc6-dev
```

---

## 💡 Tips

- Use `-march=native` in `CFLAGS` to optimize for your machine
- Use `-g` for debugging, `-O2` or `-O3` for release builds
- Combine `make` with `.PHONY`, `-j$(nproc)`, and `build/` output dirs for clean automation

---

## 📚 References

- [GCC manual](https://gcc.gnu.org/onlinedocs/)
- [GNU Make manual](https://www.gnu.org/software/make/manual/)
- [Linux From Scratch - Toolchain](https://www.linuxfromscratch.org/lfs/view/development/chapter05/toolchaintechnotes.html)

---
