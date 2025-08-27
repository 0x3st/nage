
# Nage

[![PyPI - Version](https://img.shields.io/pypi/v/nage)](https://pypi.org/project/nage/)
[![License](https://img.shields.io/github/license/0x3st/nage)](https://github.com/0x3st/nage/LICENSE)

Nage is a lightweight AI tool for your command line. Get started quickly by simply asking it a question.

Nage is a free and open source software for all its users and open source community.
## Features

- Conversational interactives without borderness
- Automatic memory and history storage and management
- No extra commands needed
- Light and easy to install

## Installation

1. You can simply use `pip install nage` but it might be unsafe.
2. Using `uv tool install nage` can prevent the problem of environgment. (Recommend)

## Usage

1. For the first time use, Nage will automactically create static files `MEMO`, `SETT` and `HIST` under the route `~/.nage/`.

2. Nage will ask you to provide your own api-key. You could also edit your settings(model, endpoint, api-key) in the file `~/.nage/SETT`.

3. When everything is set, you could start using Nage by just ask `nage how can I reboot my mac?`. Nage could deal with simple Q&A questions and command suggestions. Sometimes Nage will ask further information for comprehensive answers.

4. You can ask Nage to delete the history or memory mannually. If you don't clear them, Nage will automatically clear ONLY history. Memories will be clear IF AND ONLY IF asked by user.

5. English now is set to the universal language.

## Warnings

1. Nage don't initiatively collect you privacy and there's no malware that illegally profilling users.

2. Please read privacy policy of your api providers first before use. Nage store all your informations locally at your device. However, they will be sent to your AI provider for conitnuous conversation.

3. Some commands that Nage would collect further information like your email or option of some commands (npm create, etc.). But Nage won't collect those information.

4. Do not provide your passwords and physical address. Sensitive informations, like ID number, family address won't be proceeded.

## Changelog

- 1.2.1 *pseudo version for 1.2.0*
    - for PyPI
    - formal set up of workflows
- 1.2.0 *A nearly-wonderful new version!*
    - Code mainly by 0x3st
    - Redesigned file structure
    - Follow `PEP 7` & `PEP 8`
    - Basic functions like history, memories, continuous conversation, stream output.

- 1.0.0 *First version released.*
    - Vibe coding with `Claude Sonnect 4.`
    - Complex code and structure.

## To-dos

> Actually some are easy to do but I'm just too lazy currently :D
1. Change some configuration variables to `setting.py`

2. After (1), allow user mannually turn on or off for some critical alternatives.

3. Use regular expressions or statistical methods to deal with some logical failures.(e.g. Ask AI to change api-key)

4. Support MCP servers and function calling.
## License

[GPLv3](https://github.com/0x3st/nage/LICENSE) is applied.

Please follow the license when using or distributing it or its derivatives.