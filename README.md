# AutoForge

Welcome to **AutoForge**.

**AutoForge** is a flexible and extensible Python framework designed to streamline and enhance modern build workflows.
It provides a powerful set of tools to define and manage complete build systems — from initial setup and environment
preparation, through compilation and deployment, all the way to logging, error handling, and reporting.

At its core, AutoForge is driven by a set of simple, declarative JSON definitions, allowing teams to configure build
behavior with minimal boilerplate.

Key features include:

- Modular, CLI-driven architecture
- Standardized logging and structured error reporting
- Automated environment setup and teardown
- JSON-based configuration for repeatable builds
- Dynamic command loading for easy extension

AutoForge was built with scalability in mind — whether you're managing a small embedded project or orchestrating complex
multi-stage builds, it provides the right balance of automation, clarity, and control.

### Setup Instructions.

The following link installs a demo solution that builds the top command.
To use it, copy and paste the command below into your terminal.

```bash
curl -sSL \
  -H "Cache-Control: no-store" \
  "https://raw.githubusercontent.com/emichael72/auto_forge/main/src/auto_forge/resources/shared/bootstrap.sh" \
  | bash -s -- -n demo -w ws -s create_environment_sequence -p https://github.com/emichael72/auto_forge/tree/main/src/auto_forge/resources/samples/btop
```

### Installing the package.

To install the latest AutoForge package use the following command:

```bash
pip install git+https://github.com/emichael72/auto_forge.git --force-reinstall
```

## License

This project is licensed under the MIT License—see the LICENSE file for details.

## Acknowledgments

Thanks to everyone who has contributed to the development of this exciting project!
