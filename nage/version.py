from importlib import metadata


INSTALLATION_NOTICE = (
    "Nage is a free software under GPLv3 by 0x3st. Go Linux Do, learn AI."
)


def package_version() -> str:
    try:
        return metadata.version("nage")
    except metadata.PackageNotFoundError:
        return "0+unknown"
