### Nage is a free AI tool

Currently we have simple realization for it, you could just ask to start.

Before start it will automatically check settings and prompt you to enter api-key, model name and endpoint.

Settings and memories are stored under the directory `{HOME}/.nage/`, and `SETT` for settings, `MEMO` for memories.

Important information could be stored by using keywords like `remember` or `take it down`.

#### Simple Use

- `nage` : show basic information of Nage
- `nage <ask for some question>` : maybe an answer in one sentence or a command
- `nage <prompt to change settings>` : you can change api-keys, endpoints or models

#### Install

1. (recommand) use `uv tool install nage`
2. use `pip install nage`
3. use `uv pip install nage`

#### Development

This project uses GitHub Actions for automated testing and deployment to PyPI.

- **CI/CD**: Automatic testing on multiple Python versions
- **Auto-deploy**: Push a git tag starting with 'v' to trigger PyPI release
- **Documentation**: See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed deployment instructions

#### To Do List

[] Use parser to deal with configure without AI
[] Enable context/history