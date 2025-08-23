**Nage: A Free AI Assistant Tool**

Nage is a lightweight AI tool for your command line. Get started quickly by simply asking it a question.

**Getting Started**

On first use, Nage will automatically check your settings and prompt you for the necessary details:
- API Key
- Model Name
- Endpoint

Your settings and memories are stored locally in your `{HOME}/.nage/` directory (`SETT` for settings, `MEMO` for memories).

**Usage**

- `nage` - Shows basic information.
- `nage <your question>` - Gets an answer or runs a command.
- `nage <prompt to change settings>` - Updates your API key, endpoint, or model.

Use keywords like `remember` or `take it down` to store important info.

**Installation**

Choose one of the following methods:
1. (Recommended) `uv tool install nage`
2. `pip install nage`
3. `uv pip install nage`

**Development**

This project uses GitHub Actions for testing and deployment.
- **CI/CD**: Automated tests across Python versions.
- **Auto-deploy**: Push a tag starting with 'v' to release to PyPI.

**To Do**
- [ ] Add a parser for configuration without AI.
- [ ] Enable context/history features.