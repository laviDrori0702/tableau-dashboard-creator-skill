# Contributing

Thanks for your interest in improving the Tableau Dashboard Creator skill!

## How to Contribute

### Reporting Issues

- Open a [GitHub Issue](../../issues) with a clear description of the problem
- Include which step of the workflow failed (Step 0–E)
- Attach relevant input files if possible (PDR, sample data) — **never share credentials or `.env` files**

### Suggesting Features

- Open an issue with the `enhancement` label
- Describe the use case and expected behavior
- Examples: new database connectors, chart types, layout templates, branding sources

### Submitting Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Make your changes
4. Test the skill end-to-end with the demo project (see `demo/`)
5. Submit a PR with a clear description of what changed and why

### Areas Where Help is Welcome

- **Database connectors** — Adding support for new databases (MySQL, BigQuery, etc.) in `skill/tableau-dashboard-creator/scripts/`
- **TWB generation** — Step E is experimental; improvements to XML fidelity are valuable
- **Layout templates** — New dashboard layout patterns in the design tokens system
- **Documentation** — Improving the README, adding tutorials, or translating

### Code Style

- Python: PEP 8, type hints, Google-style docstrings
- Markdown: Keep reference docs concise and structured
- No print statements — use the `logging` module

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).