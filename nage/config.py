import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping
from urllib.parse import urlsplit

from .errors import ConfigurationError
from .paths import AppPaths

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_ENDPOINT = "https://api.deepseek.com/v1"


@dataclass(frozen=True)
class AppConfig:
    model: str = DEFAULT_MODEL
    endpoint: str = DEFAULT_ENDPOINT
    api_key: str = field(default="", repr=False)
    memory_enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ConfigurationError("Model name must be a non-empty string.")
        if not isinstance(self.endpoint, str):
            raise ConfigurationError("API endpoint must be a string.")
        self._validate_endpoint(self.endpoint)
        if not isinstance(self.api_key, str):
            raise ConfigurationError("API key must be a string.")
        if not isinstance(self.memory_enabled, bool):
            raise ConfigurationError("memory_enabled must be a boolean.")

    @staticmethod
    def _validate_endpoint(endpoint: str) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigurationError(
                "API endpoint must be an absolute HTTP or HTTPS URL."
            )
        if parsed.username is not None or parsed.password is not None:
            raise ConfigurationError(
                "API endpoint must not contain embedded credentials."
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "AppConfig":
        if not isinstance(data, Mapping):
            raise ConfigurationError("Configuration root must be a JSON object.")
        return cls(
            model=data.get("model", DEFAULT_MODEL),
            endpoint=data.get("endpoint", DEFAULT_ENDPOINT),
            api_key=data.get("api_key", ""),
            memory_enabled=data.get("memory_enabled", True),
        )

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "endpoint": self.endpoint,
            "api_key": self.api_key,
            "memory_enabled": self.memory_enabled,
        }

    def redacted_mapping(self) -> Dict[str, Any]:
        data = self.to_mapping()
        data["api_key"] = "***" if self.api_key else ""
        return data


class ConfigStore:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def load(self) -> AppConfig:
        path = self.paths.config_file
        if not path.exists():
            return AppConfig()
        if not path.is_file():
            raise ConfigurationError(
                "Nage configuration path is not a file: {0}".format(path)
            )

        try:
            with path.open("r", encoding="utf-8") as config_file:
                data = json.load(config_file)
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(
                "Unable to read Nage configuration {0}: {1}".format(path, exc)
            ) from exc

        return AppConfig.from_mapping(data)

    def save(self, config: AppConfig) -> None:
        self.paths.ensure()
        target = self.paths.config_file
        temporary_path = None

        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".config-", suffix=".tmp", dir=str(target.parent)
            )
            temporary_path = target.parent / os.path.basename(temporary_name)
            if os.name != "nt":
                os.chmod(temporary_name, 0o600)

            with os.fdopen(file_descriptor, "w", encoding="utf-8") as output:
                json.dump(
                    config.to_mapping(),
                    output,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())

            os.replace(str(temporary_path), str(target))
            temporary_path = None
            if os.name != "nt":
                target.chmod(0o600)
        except OSError as exc:
            raise ConfigurationError(
                "Unable to save Nage configuration {0}: {1}".format(target, exc)
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
