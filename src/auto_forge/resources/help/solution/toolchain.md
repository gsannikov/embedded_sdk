# toolchain.md — Defining and Using Toolchains

## 🔧 Purpose

Toolchains in AutoForge are defined in `solution.json` under the `"tool_chains"` section. They describe how to locate
and use external cross-compilation environments, such as GCC for AArch64 or ARM targets.

A toolchain entry specifies:

- The compiler prefix and path
- Target architecture and platform
- CMake-related options
- Required tools (gcc, cmake, ninja)

Toolchains are reusable across configurations (e.g., Debug, Release) and provide the baseline compiler environment.

---

## 🧱 Defining a Toolchain

In your `solution.json`:

```jsonc
"tool_chains": [
    {
        "name": "Linaro AArch64 Cross Toolchain",
        "platform": "linux",
        "description": "https://releases.linaro.org/components/toolchain/binaries/7.1-2017.05/",
        "architecture": "aarch64",
        "build_system": "cmake",
        "tool_prefix": "aarch64-linux-gnu-",
        "tool_base_path": "$AF_TOOL_CHAINS/gcc-linaro-7.5.0-2019.12-x86_64_aarch64-linux-gnu",
        "tool_bins_path": "<$ref_tool_base_path>/bin",
        "tool_sysroot": "<$ref_tool_base_path>/aarch64-linux-gnu/libc",
        "required_tools": {
            "cmake": {
                "path": "cmake",
                "version": ">=3.2",
                "help": "builder/cmake.md",
                "options": [
                    "-G",
                    "Ninja",
                    "-DCMAKE_SYSTEM_NAME=Linux",
                    "-DCMAKE_SYSTEM_PROCESSOR=aarch64",
                    "-DCMAKE_C_COMPILER=<$ref_tool_bins_path>/<$ref_tool_prefix>gcc",
                    "-DCMAKE_CXX_COMPILER=<$ref_tool_bins_path>/<$ref_tool_prefix>g++",
                    "-DCMAKE_SYSROOT=<$ref_tool_sysroot>",
                    "-DCMAKE_FIND_ROOT_PATH=<$ref_tool_sysroot>",
                    "-DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER",
                    "-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY",
                    "-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY"
                ]
            },
            "gcc": {
                "path": "<$ref_tool_base_path>/bin/<$ref_tool_prefix>gcc",
                "version": ">=7.5",
                "help": "builder/arm_cross_gcc.md"
            },
            "ninja": {
                "path": "ninja",
                "version": ">=1.10",
                "help": "builder/ninja.md"
            }
        }
    }
]
```

---

## ⚙️ Merging with Configuration Options

Each build configuration (like Debug, Release, etc.) defines its own `"compiler_options"` block. These options are *
*merged with the toolchain's CMake options** at build time.

This allows the toolchain to define system-level paths, compilers, and target properties, while the configuration
contributes things like optimization level and feature toggles.

Example:

```jsonc
"compiler_options": [
    "-S .",
    "-B <$ref_build_path>",
    "-DCMAKE_BUILD_TYPE=Debug",
    "-DMMG=1",
    "-DSOURCES_ROOT_PATH=<$ref_execute_from>",
    "-DDO_COMPILE_INFRA=1",
    "-DZEPHYR_BUILD=1",
    // Explicit debug flags
    "-DCMAKE_C_FLAGS_DEBUG=-O0 -g3 -ggdb",
    "-DCMAKE_CXX_FLAGS_DEBUG=-O0 -g3 -ggdb"
]
```

---

## ✅ Final Merged Command (Example)

After merging toolchain options and configuration-specific `compiler_options`, the actual CMake configuration call may
look like:

```bash
cmake -S . -B build/debug \
  -G Ninja \
  -DCMAKE_SYSTEM_NAME=Linux \
  -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
  -DCMAKE_C_COMPILER=/opt/toolchains/bin/aarch64-none-linux-gnu-gcc \
  -DCMAKE_CXX_COMPILER=/opt/toolchains/bin/aarch64-none-linux-gnu-g++ \
  -DCMAKE_SYSROOT=/opt/toolchains/aarch64-none-linux-gnu/libc \
  -DCMAKE_FIND_ROOT_PATH=/opt/toolchains/aarch64-none-linux-gnu/libc \
  -DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER \
  -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY \
  -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY \
  -DCMAKE_BUILD_TYPE=Debug \
  -DMMG=1 \
  -DSOURCES_ROOT_PATH=src \
  -DDO_COMPILE_INFRA=1 \
  -DZEPHYR_BUILD=1 \
  -DCMAKE_C_FLAGS_DEBUG=-O0 -g3 -ggdb \
  -DCMAKE_CXX_FLAGS_DEBUG=-O0 -g3 -ggdb
```

---

## 🧠 Tips

- Place toolchains in a versioned directory under `$HOME/.auto_forge/tool_chains`.
- Use `<$ref_...>` placeholders to avoid hardcoding paths and prefixes.
- Use `"tool_prefix"` to define the compiler triplet (e.g., `aarch64-none-linux-gnu-`).
- Keep toolchain options minimal and general; use `compiler_options` for config-specific behavior.

