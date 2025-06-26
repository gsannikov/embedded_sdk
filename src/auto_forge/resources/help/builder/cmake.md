# CMake — Build System Guide

## 🔧 Description:

CMake is a cross-platform, open-source build system generator. It generates native build files for Ninja, Make, or IDEs,
using a declarative input file (CMakeLists.txt).

This toolchain uses CMake in conjunction with the Ninja generator to configure and build the project in a two-phase
process.

---

## 🧱 Configuration Phase (Required):

cmake -S . -B <build_dir> \
-G Ninja \
-DCMAKE_TOOLCHAIN_FILE=<toolchain_file.cmake> \
-DCMAKE_BUILD_TYPE=Debug \
-D<key1>=<value1> \
-D<key2>=<value2>

- `-S .` specifies the source directory (root of the project).
- `-B <build_dir>` is the build directory where artifacts and config files are generated.
- `-G Ninja` tells CMake to use Ninja as the build generator.
- `-D...` passes CMake cache variables or user-defined options.

---

## ⚙️ Build Phase:

```bash
ninja -C <build_dir> [-j N] [-v]
```

or:

cmake --build <build_dir> -- -j<N> -v

- `-j<N>` sets the number of parallel jobs (e.g. `-j8`)
- `-v` shows full compiler commands and errors in real time

---

## 🔍 Common CMake Cache Variables

| Variable                            | Purpose                                       |
|-------------------------------------|-----------------------------------------------|
| `CMAKE_BUILD_TYPE`                  | One of: `Debug`, `Release`, `RelWithDebInfo`  |
| `CMAKE_TOOLCHAIN_FILE`              | Path to the CMake toolchain file              |
| `CMAKE_C_COMPILER`                  | Full path to the C compiler                   |
| `CMAKE_CXX_COMPILER`                | Full path to the C++ compiler                 |
| `CMAKE_SYSROOT`                     | Root of the target sysroot                    |
| `CMAKE_FIND_ROOT_PATH`              | Root paths for CMake find_* commands          |
| `CMAKE_FIND_ROOT_PATH_MODE_PROGRAM` | Set to `NEVER` for cross-compilation          |
| `CMAKE_FIND_ROOT_PATH_MODE_LIBRARY` | Set to `ONLY` for isolated sysroot lookup     |
| `CMAKE_FIND_ROOT_PATH_MODE_INCLUDE` | Set to `ONLY` to restrict include path search |
| `CMAKE_VERBOSE_MAKEFILE`            | Set to `ON` to see full build commands        |

---

## 🛠 Example (Linux + Ninja + Arm cross compile):

cmake -S . -B build/debug -G Ninja \
-DCMAKE_TOOLCHAIN_FILE=cmake/toolchain-arm64.cmake \
-DCMAKE_BUILD_TYPE=Debug \
-DSOME_FEATURE=ON \
-DMMG=1

ninja -C build/debug -v

---

## ✅ Notes:

- Always run `cmake -S -B` **once before** building with Ninja.
- For clean builds, `rm -rf <build_dir>` before reconfiguring.
- Use `cmake --build` for portable scripting (`ninja` underneath).

