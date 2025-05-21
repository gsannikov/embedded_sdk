# Troubleshooting Missing `make` Binary

This guide helps diagnose and resolve errors like:

```
make: command not found
```

or

```
/usr/bin/env: ‘make’: No such file or directory
```

---

## 🔍 Step 1: Check if `make` is installed

Run in terminal:

```
which make
```

Expected output:

```
/usr/bin/make
```

If it prints nothing or an error — `make` is missing.

To check version:

```
make --version
```

---

## 🛠 Step 2: Install `make` on your system

### Fedora / RHEL / CentOS:

```bash
sudo dnf install make
```

### Ubuntu / Debian / WSL:

```bash
sudo apt update
sudo apt install make
```

### Arch / Manjaro:

```bash
sudo pacman -S make
```

### macOS (via Xcode CLI tools):

```bash
xcode-select --install
```

If using Homebrew:

```bash
brew install make
```

---

## 🧪 Step 3: Environment and PATH issues

### If `make` is installed but still not found:

- Run:
  ```bash
  echo $PATH
  ```

- Verify that `/usr/bin` or wherever `make` lives is included.

- You can locate the actual binary with:
  ```bash
  find / -name make -type f 2>/dev/null | grep bin
  ```

- If `make` exists at an unusual path, export it manually:

  ```bash
  export PATH=/custom/path/to/bin:$PATH
  ```

---

## 🧰 Step 4: Portable Make Wrapper (for SDKs / CI)

If you're packaging a cross-platform SDK or automation tool:

- Define the path in a config file:
  ```json
  {
    "required_tools": {
      "make": [
        "/usr/bin/make",
        ">=3.16",
        "builder/make.txt"
      ]
    }
  }
  ```

- Or allow override via environment variable:
  ```bash
  MAKE_CMD="${MAKE_CMD:-make}"
  $MAKE_CMD all
  ```

---

## ⚙️ Step 5: Make is present but fails during build

Common issues:

- Makefile uses GNU extensions → ensure `make` is GNU Make (not BSD Make on macOS)
- Permissions errors → ensure source and output directories are writable
- Recursive `make` or invalid paths → enable debug:
  ```bash
  make V=1
  ```

---

## 📚 References

- [GNU Make manual](https://www.gnu.org/software/make/manual/make.html)
- [Fedora `make` package](https://packages.fedoraproject.org/pkgs/make/)
- [Debian `make`](https://packages.debian.org/search?keywords=make)

---

## ✅ Quick Summary

| System        | Install Command           |
|---------------|---------------------------|
| Fedora        | `sudo dnf install make`   |
| Ubuntu/Debian | `sudo apt install make`   |
| Arch Linux    | `sudo pacman -S make`     |
| macOS         | `xcode-select --install`  |
| WSL           | Same as underlying distro |

---
