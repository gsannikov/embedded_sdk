# 🛠️ Welcome to AutoForge

Hello, curious human. You've just unlocked access to **AutoForge** — a build system that doesn’t scream in Bash or
mysteriously break after the fifth shell script.

Whether you're a firmware dev, CI engineer (yes, you're definitely engineers 💼), or someone who just likes their builds
fast, clean, and deterministic — you're in the right place.

---

## 🧠 What *Is* AutoForge?

AutoForge is a **modular, Python-powered build orchestration system**.

Think of it as the Swiss Army knife of build systems:

- Sharp.
- Compact.
- Extensible.
- And no, you won’t poke your eye out with this one.

---

## 🐍 Python, Not Piles of Scripts

AutoForge isn’t duct tape wrapped around shell scripts.

It's a **proper Python package**, installable, testable, and written like software should be:

- 🧼 Enforced coding standards
- 🔍 Linters and formatters on patrol
- 🧩 Class-based plugin interfaces for dynamic command loading and build backends

Currently, it supports:

- `make` (for the old-school hardcore devs 🧓)
- `cmake` (for the slightly more civilized crowd 👷‍♂️)

Adding more? Super easy. Contributions welcome!

---

## 🤝 Want to Help?

If you’re the kind of dev who:

- Adds docstrings without being told,
- Finds joy in making things extensible,
- Or just wants to see `ninja`, `bazel`, or `kitchen_sink` added as a backend...

We'd love to have you on board. Fork us, clone us, or just lurk in the shadows and read the code.

---

## 🚀 Getting Started

### Bootstrap your workspace in one line

```bash
curl -sSL \
  -H "Cache-Control: no-store" \
  "https://raw.githubusercontent.com/emichael72/auto_forge/main/src/auto_forge/resources/shared/bootstrap.sh" \
  | bash -s -- -n demo -w ws -s create_environment_sequence \
             -p https://github.com/emichael72/auto_forge/tree/main/src/auto_forge/resources/samples/btop
```

### What these flags mean

- `-n demo`: The name of the solution to use, defined in your project’s main `solution.jsonc`:
  ```jsonc
  {
    // Solutions
    "solutions": [
        {
            "name": "demo",
            ...
        }
    ]
  }
  ```

- `-w ws`: The name/path of the new workspace. In this case, a directory named `ws` will be created in your current
  location.

- `-s create_environment_sequence`: Tells AutoForge to follow a pre-defined boot sequence from the solution file, for
  example:
  ```jsonc
  {
    "create_environment_sequence": "<$include>environment.jsonc"
  }
  ```
  This `environment.jsonc` is a recipe that handles everything: prerequisites, downloads, cloning — the works.

- `-p <package>`: Points to a package (local path, `.zip`, or remote URL/repo) that contains the `solution.json` and all
  referenced recipes.

---

Welcome aboard, builder. 🧱🧠
