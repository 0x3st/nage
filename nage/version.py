from importlib import metadata


def package_version() -> str:
    try:
        return metadata.version("nage")
    except metadata.PackageNotFoundError:
        return "0+unknown"
