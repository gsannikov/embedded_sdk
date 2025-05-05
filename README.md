# AutoForge

Welcome to **AutoForge**, the SDK companion Bob the Builder.

**AutoForge** is a flexible and extensible Python framework designed to streamline and enhance modern build workflows. It provides a powerful set of tools to define and manage complete build systems — from initial setup and environment preparation, through compilation and deployment, all the way to logging, error handling, and reporting.

At its core, AutoForge is driven by a set of simple, declarative JSON definitions, allowing teams to configure build behavior with minimal boilerplate.

Key features include:

- Modular, CLI-driven architecture
- Standardized logging and structured error reporting
- Automated environment setup and teardown
- JSON-based configuration for repeatable builds
- Dynamic command loading for easy extension

AutoForge was built with scalability in mind — whether you're managing a small embedded project or orchestrating complex multi-stage builds, it provides the right balance of automation, clarity, and control.

### Setup Instructions.

The following is a sample link that installs the demo solution.
To use it, make sure you have exported your GitHub token to the environment as GITHUB_TOKEN.

```bash
curl -sSL \
  -H "Authorization: token ${GITHUB_TOKEN}" \
  -H "Cache-Control: no-store" \
  "https://raw.githubusercontent.com/emichael72/auto_forge/main/src/auto_forge/resources/demo_project/auto_boot.sh" \
  | bash -s -- -w ./ws -s https://github.com/emichael72/auto_forge/tree/main/src/auto_forge/resources/demo_project -t $GITHUB_TOKEN
```

### Installing the package.

```bash
pip install git+https://github.com/emichael72/auto_forge.git --force-reinstall
```

## License

This project is licensed under the MIT License—see the LICENSE file for details.

## Acknowledgments

Thanks to everyone who has contributed to the development of this exciting project!
