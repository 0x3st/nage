"""Removed Nage 1.x SETT/MEMO/HIST compatibility module."""


class Setting:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError(
            "Setting was removed in Nage 2. Use AppConfig and the SQLite stores."
        )
