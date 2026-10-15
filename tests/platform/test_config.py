"""Platform configuration tests."""

from __future__ import annotations

from pathlib import Path

from src.platform.config import PlatformConfig, find_repo_root, load_platform_config


def test_repo_root_contains_configs() -> None:
    root = find_repo_root()
    assert (root / "configs" / "platform.yaml").exists()


def test_platform_config_defaults() -> None:
    cfg = load_platform_config()
    assert cfg.platform.name == "enterprise-analytics-platform"
    assert cfg.statistics.fdr_alpha == 0.05
    assert cfg.statistics.interpretation == "associational"
    assert cfg.visibility.demand_component_available is False
    assert cfg.action_engine.priority_thresholds["high"] > 0
    assert cfg.sharing.read_only is True


def test_resolve_paths_are_absolute() -> None:
    cfg = load_platform_config()
    assert cfg.resolve("data/gold").is_absolute()
    assert cfg.resolve(cfg.paths.silver_dir).name == "silver"


def test_env_expansion(tmp_path: Path) -> None:
    config_file = tmp_path / "platform.yaml"
    config_file.write_text(
        "platform:\n  name: ${MY_PLAT_NAME:-fallback-name}\n",
        encoding="utf-8",
    )
    load_platform_config.cache_clear()
    cfg = load_platform_config(str(config_file))
    assert cfg.platform.name == "fallback-name"

    import os

    os.environ["MY_PLAT_NAME"] = "expanded-name"
    try:
        load_platform_config.cache_clear()
        cfg2 = load_platform_config(str(config_file))
        assert cfg2.platform.name == "expanded-name"
    finally:
        del os.environ["MY_PLAT_NAME"]
        load_platform_config.cache_clear()


def test_config_is_cached() -> None:
    first = load_platform_config()
    second = load_platform_config()
    assert first is second
    assert isinstance(first, PlatformConfig)
