#!/usr/bin/env python3
"""Effective configuration for zotero-daily-papers.

Merge order (later wins):
  1. _zotero_shared/config.default.json
  2. ~/.config/zotero-daily-papers/config.json   (XDG_CONFIG_HOME respected)
  3. the JSON file named by ZDP_CONFIG
  4. env: ZOTERO_API_KEY, ZOTERO_LIBRARY_ID, ZOTERO_LIBRARY_TYPE

`language` ("zh" or "en", env ZDP_LANGUAGE) picks the language of generated notes.
With "zh" the default pending collection is named "待定" instead of "Pending"; an explicitly
configured pending_collection always wins.

Run directly to print the effective config (API key masked) and runtime paths.
`--init` writes a personal config template once and never overwrites it.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SHARED_DIR.parent.parent  # Legacy diagnostic field only.
DEFAULT_CONFIG_PATH = SHARED_DIR / "config.default.json"
LANGUAGES = ("zh", "en")
# Localized defaults that sit below every personal override.
LOCALIZED_DEFAULTS = {"zh": {"zotero": {"pending_collection": "待定"}}}


def config_home() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
    return root / "zotero-daily-papers"


def personal_config_path() -> Path:
    return config_home() / "config.json"


def history_path() -> Path:
    return config_home() / "history.json"


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def validate_language(value: object) -> str:
    if value not in LANGUAGES:
        raise ValueError(f"language must be one of {', '.join(LANGUAGES)}, got {value!r}")
    return value


@lru_cache(maxsize=1)
def load_config() -> dict:
    config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    paths = [personal_config_path()]
    explicit = os.environ.get("ZDP_CONFIG")
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if not explicit_path.is_file():
            raise FileNotFoundError(f"ZDP_CONFIG does not point to a file: {explicit_path}")
        paths.append(explicit_path)
    overrides = []
    for path in paths:
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError(f"Configuration must be a JSON object: {path}")
            overrides.append(loaded)
    # The language is resolved first so localized defaults sit below every personal override.
    language = config.get("language", "en")
    for loaded in overrides:
        language = loaded.get("language", language)
    language = validate_language(os.environ.get("ZDP_LANGUAGE", "").strip() or language)
    _deep_merge(config, copy.deepcopy(LOCALIZED_DEFAULTS.get(language, {})))
    for loaded in overrides:
        _deep_merge(config, loaded)
    config["language"] = language

    zot = config["zotero"]
    for env, key in (("ZOTERO_API_KEY", "api_key"), ("ZOTERO_LIBRARY_ID", "library_id"),
                     ("ZOTERO_LIBRARY_TYPE", "library_type"), ("ZOTERO_BACKEND", "backend")):
        if os.environ.get(env):
            zot[key] = os.environ[env].strip()
    classification_mode(zot)
    return config


def classification_mode(cfg: dict) -> str:
    mode = cfg.get("classification_mode", "pending")
    if mode not in ("pending", "existing"):
        raise ValueError("zotero.classification_mode must be 'pending' or 'existing'")
    return mode


def zotero_config() -> dict:
    return load_config()["zotero"]


def language() -> str:
    return load_config()["language"]


def keyword_group(entry: object, where: str) -> list[str]:
    """A keyword entry is one term or a list of synonyms that scores at most once."""
    terms = [entry] if isinstance(entry, str) else entry
    if not isinstance(terms, list) or not terms or not all(isinstance(t, str) and t.strip() for t in terms):
        raise ValueError(f"{where}: each keyword must be a non-empty string or a non-empty list of strings")
    return [t.strip() for t in terms]


def daily_papers_config() -> dict:
    """Daily-paper settings with keyword groups / interests derived from research directions."""
    dp = copy.deepcopy(load_config()["daily_papers"])
    if "min_score" in dp:  # legacy name from older personal configs
        dp.setdefault("recall_min_score", dp.pop("min_score"))
    # Each direction is scored on its own groups; extra_keywords form one more scoring unit.
    units = [(d.get("id", str(i)), d.get("keywords", []), f"directions[{d.get('id', i)}].keywords")
             for i, d in enumerate(dp.get("directions", []))]
    if dp.get("extra_keywords"):
        units.append(("extra", dp["extra_keywords"], "extra_keywords"))
    direction_groups: dict[str, list[list[str]]] = {}
    for unit_id, entries, where in units:
        seen: set[str] = set()
        groups = []
        for entry in entries:
            # Within one direction a term counts once, even if listed in two groups.
            group = [t for t in keyword_group(entry, where) if not (t.casefold() in seen or seen.add(t.casefold()))]
            if group:
                groups.append(group)
        direction_groups[unit_id] = groups
    dp["direction_groups"] = direction_groups
    dp["keyword_groups"] = [g for groups in direction_groups.values() for g in groups]
    dp["keywords"] = list(dict.fromkeys(t for g in dp["keyword_groups"] for t in g))
    # Focus topics add a bonus on top of direction scoring; they are kept separate on purpose.
    dp["focus_groups"] = [keyword_group(e, "focus_keywords") for e in dp.get("focus_keywords", [])]
    bonus = dp.get("focus_bonus", 3)
    if type(bonus) not in (int, float) or bonus < 0:
        raise ValueError("daily_papers.focus_bonus must be a non-negative number")
    quality = dp.get("quality_boost", {})
    venues = quality.get("venues", [])
    if not isinstance(venues, list) or not all(isinstance(v, str) and v.strip() for v in venues):
        raise ValueError("daily_papers.quality_boost.venues must be a list of venue names")
    for key in ("venue_multiplier", "accepted_multiplier"):
        if type(quality.get(key, 1)) not in (int, float) or quality.get(key, 1) < 1:
            raise ValueError(f"daily_papers.quality_boost.{key} must be a number >= 1")
    dp["research_interests"] = [
        f"[{d['id']}] {d['name']}: "
        + ", ".join("/".join(keyword_group(e, d["id"])) for e in d.get("keywords", []))
        for d in dp.get("directions", [])
    ]
    return dp


def directions() -> list[dict]:
    return load_config()["daily_papers"].get("directions", [])


def temp_dir() -> Path:
    if os.environ.get("ZDP_TEMP_DIR"):
        path = Path(os.environ["ZDP_TEMP_DIR"]).expanduser()
    else:
        path = Path(tempfile.gettempdir()) / "zotero-daily-papers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_file_path(filename: str) -> Path:
    return temp_dir() / filename


def load_history() -> list[dict]:
    path = history_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_history(entries: list[dict]) -> None:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def personal_template(lang: str = "en") -> dict:
    return {
        "language": lang,
        "zotero": {
            "backend": "local",
            "classification_mode": "pending",
            "library_type": "user",
            "library_id": "",
            "api_key": "",
        },
        "daily_papers": {
            "top_n": 10,
            "extra_keywords": [],
        },
    }


def update_personal_config(change) -> Path:
    """Apply `change(config)` to the personal config atomically, keeping every other setting."""
    path = personal_config_path()
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(config, dict) or not isinstance(config.get("zotero", {}), dict):
        raise ValueError("Personal configuration must contain a zotero object")
    change(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(config, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)
    load_config.cache_clear()
    return path


def set_language(lang: str) -> Path:
    validate_language(lang)
    return update_personal_config(lambda config: config.__setitem__("language", lang))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--init", action="store_true", help="Create the personal config template if absent")
    parser.add_argument("--lang", choices=LANGUAGES,
                        help="With --init: template language (default: current language)")
    parser.add_argument("--set-language", choices=LANGUAGES,
                        help="Set personal language for generated notes and localized defaults")
    parser.add_argument("--set-classification", choices=("pending", "existing"),
                        help="Set personal default: pending collection or match existing collections")
    args = parser.parse_args()
    if args.set_language:
        path = set_language(args.set_language)
        print(f"Saved language={args.set_language}: {path}")
        if not args.set_classification:
            return
    if args.set_classification:
        path = update_personal_config(
            lambda config: config.setdefault("zotero", {}).__setitem__("classification_mode", args.set_classification))
        print(f"Saved classification_mode={args.set_classification}: {path}")
        return
    if args.init:
        path = personal_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(personal_template(args.lang or load_config()["language"]), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.chmod(path, 0o600)
            print(f"Created personal configuration: {path}")
        except FileExistsError:
            print(f"Existing configuration preserved: {path}")
        return

    config = copy.deepcopy(load_config())
    key = config["zotero"].get("api_key") or ""
    config["zotero"]["api_key"] = (key[:3] + "…" + key[-2:]) if key else ""
    config["zotero"]["configured"] = (config["zotero"].get("backend", "local") == "local" or bool(key and config["zotero"].get("library_id")))
    config["runtime"] = {
        "python": sys.executable,
        "project_root": str(PROJECT_ROOT),
        "skill_dir": str(PROJECT_ROOT),  # Compatibility for older callers.
        "temp_dir": str(temp_dir()),
        "personal_config": str(personal_config_path()),
        "history": str(history_path()),
    }
    print(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
