"""Hub settings from HUB_* environment variables and HUB_CONFIG_FILE TOML."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, override

from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

_REPO = Path(__file__).resolve().parents[2]


class _TomlSource(PydanticBaseSettingsSource):
    @override
    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    @override
    def __call__(self) -> dict[str, Any]:
        path = os.getenv("HUB_CONFIG_FILE")
        if not path or not Path(path).is_file():
            return {}
        config = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        return {"config": config, "brand": config.get("brand", "Application Hub")}


class _Environment(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUB_", extra="ignore")

    brand: str = "Application Hub"
    config: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _TomlSource(settings_cls),
            file_secret_settings,
        )


@dataclass(frozen=True)
class Settings:
    brand: str
    config: dict[str, Any]
    asset_dirs: tuple[Path, ...]


def load_settings() -> Settings:
    env = _Environment()
    root = Path(os.environ.get("HUB_PLATFORM_DIR", _REPO / "platform"))
    dirs = os.environ.get("HUB_ASSET_DIRS")
    asset_dirs = (
        tuple(Path(p) for p in dirs.split(os.pathsep) if p)
        if dirs
        else (root / "assets/shell", root / "assets/web")
    )
    return Settings(env.brand, env.config, asset_dirs)
