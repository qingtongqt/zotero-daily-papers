#!/usr/bin/env python3
"""Install the three skills and their shared runtime for an agent.

  python3 install.py --host claude                 # user level:    ~/.claude/skills
  python3 install.py --host codex --project [DIR]  # project level: DIR/.agents/skills (DIR defaults to .)

The skills are copied, so the install does not depend on this checkout at run time; rerun the
same command after updating it. Each install keeps its own settings in
`_zotero_shared/config.json`, which a rerun preserves. `--lang` (en by default, or zh) picks the
language of the skills and of the notes they write, and is saved to that file.
"""
import argparse
import importlib.util
import json
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SKILLS = ('zotero-daily-papers', 'zotero-paper-save', 'zotero-paper-reader')
SHARED = '_zotero_shared'
SKILLS_DIR = PROJECT_ROOT / 'skills'
SHARED_SOURCE = SKILLS_DIR / SHARED
# Source directory suffix per language; installs always use the plain skill name.
LANGUAGES = {'zh': '', 'en': '.en'}
# Skill directory per host, relative to the home directory (user level) or the project.
HOST_DIRS = {'claude': Path('.claude/skills'), 'codex': Path('.agents/skills')}
MARKER = '.zdp-install.json'
CONFIG = 'config.json'  # Per-install settings inside _zotero_shared; never overwritten.
COPY_IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', CONFIG)


def host_target(host: str, project: Path | None = None) -> Path:
    """User level without `project`; project level inside `project` otherwise."""
    if host not in HOST_DIRS:
        raise ValueError(f'Unsupported host: {host}. Choose one of: {", ".join(HOST_DIRS)}')
    base = Path.home() if project is None else project.expanduser().resolve()
    if project is not None and not base.is_dir():
        raise ValueError(f'Project directory does not exist: {base}')
    return base / HOST_DIRS[host]


def sources(lang: str) -> dict[str, Path]:
    if lang not in LANGUAGES:
        raise ValueError(f'Unsupported language: {lang}. Choose one of: {", ".join(LANGUAGES)}')
    return {**{name: SKILLS_DIR / (name + LANGUAGES[lang]) for name in SKILLS}, SHARED: SHARED_SOURCE}


def read_marker(path: Path) -> dict | None:
    try:
        data = json.loads((path / MARKER).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _backup(destination: Path) -> Path:
    return destination.with_name(destination.name + '.zdp-old')


def recover(destination: Path) -> None:
    """Finish or undo an interrupted update, so the previous copy (and its config) is never lost."""
    backup = _backup(destination)
    if backup.is_dir() and read_marker(backup) is not None:
        if destination.exists() or destination.is_symlink():
            shutil.rmtree(backup)  # The update completed; only the cleanup was missed.
        else:
            backup.rename(destination)  # The update stopped halfway; restore the previous copy.


def install(target: Path, *, lang: str = 'en', dry_run: bool = False,
            update: bool = False) -> list[tuple[str, Path]]:
    """Copy the skills into `target`; returns (action, path) for each entry."""
    planned = sources(lang)
    target = target.expanduser().resolve()
    if target == SKILLS_DIR or SKILLS_DIR in target.parents:
        raise ValueError('Target must not be inside the skill sources (skills/)')
    if target.exists() and not target.is_dir():
        raise ValueError(f'Target is not a directory: {target}')
    if not dry_run:
        for name in planned:
            recover(target / name)
    actions = []
    # Check every entry before changing any of them.
    for name in planned:
        destination = target / name
        marker = read_marker(destination) if destination.is_dir() and not destination.is_symlink() else None
        if marker is not None:
            if name != SHARED and marker.get('lang') != lang and not update:
                raise ValueError(f'{destination} is a "{marker.get("lang")}" install. '
                                 f'Add --update to switch it to "{lang}".')
            actions.append(('update', destination))
        elif destination.exists() or destination.is_symlink():
            raise ValueError(f'Refusing to replace existing path not installed by this script: {destination}')
        else:
            actions.append(('copy', destination))
    if dry_run:
        return actions
    target.mkdir(parents=True, exist_ok=True)
    for _, destination in actions:
        staging = destination.with_name(destination.name + '.zdp-new')
        if staging.exists():
            shutil.rmtree(staging)  # Left over from an interrupted install.
        shutil.copytree(planned[destination.name], staging, ignore=COPY_IGNORE)
        (staging / MARKER).write_text(json.dumps({'lang': lang}) + '\n', encoding='utf-8')
        if (destination / CONFIG).is_file():
            shutil.copy2(destination / CONFIG, staging / CONFIG)  # Keep this install's settings.
        # Keep the previous copy until the new one is in place; recover() handles an interruption.
        if destination.exists():
            destination.rename(_backup(destination))
        staging.rename(destination)
        if _backup(destination).exists():
            shutil.rmtree(_backup(destination))
    return actions


def save_language(target: Path, lang: str) -> Path:
    """Persist the language in the installed config, keeping every other setting."""
    script = target / SHARED / 'zdp_config.py'
    spec = importlib.util.spec_from_file_location('installed_zdp_config', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.set_language(lang)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', choices=tuple(HOST_DIRS), required=True,
                        help='Agent to install for: claude (.claude/skills) or codex (.agents/skills)')
    parser.add_argument('--project', type=Path, nargs='?', const=Path('.'), metavar='DIR',
                        help='Install at project level into DIR (default: current directory) '
                             'instead of the home directory')
    parser.add_argument('--lang', choices=tuple(LANGUAGES), default='en',
                        help='Skill and note language: en (English, default) or zh (Chinese)')
    parser.add_argument('--update', action='store_true', help='Switch an existing install to --lang')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be installed')
    args = parser.parse_args()
    try:
        target = host_target(args.host, args.project)
        actions = install(target, lang=args.lang, dry_run=args.dry_run, update=args.update)
        config = None if args.dry_run else save_language(target.expanduser().resolve(), args.lang)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    prefix = '[dry-run] ' if args.dry_run else ''
    for action, path in actions:
        print(f'{prefix}{action}: {path}')
    print(f'{prefix}language: {args.lang}' + (f' (saved to {config})' if config else ''))


if __name__ == '__main__':
    main()
