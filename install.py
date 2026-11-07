#!/usr/bin/env python3
"""Install the three peer skills and their shared runtime into an agent's skill directory.

The skills are copied, so no symbolic-link support is needed (Windows works as is) and the
installed skills do not depend on this checkout at run time. After updating this checkout,
rerun the same command to refresh the copies.

`--lang en` (default) installs the English skills (`skills/<name>.en`); `--lang zh` installs the
Chinese skills (`skills/<name>`). Both use the same shared runtime in `skills/_zotero_shared`, and
the chosen language is also saved to the personal config so generated notes match.
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
# Skill discovery directory per host, relative to the home directory (user scope)
# or to the project directory (project scope).
HOST_DIRS = {'claude': Path('.claude/skills'), 'codex': Path('.agents/skills')}
MARKER = '.zdp-install.json'
COPY_IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc')


def host_target(host: str, project: Path | None = None) -> Path:
    """User scope without `project`; project scope inside `project` otherwise."""
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


def _required(name: str) -> str:
    return 'config.default.json' if name == SHARED else 'SKILL.md'


def check_bundle(lang: str) -> None:
    """Fail early on a broken checkout instead of installing unusable skills."""
    for name, source in sources(lang).items():
        required = source / _required(name)
        if not required.is_file():
            raise ValueError(f'Missing bundle resource: {required}')
    script = SHARED_SOURCE / 'zdp_config.py'
    if script.stat().st_size < 1024:
        # A checkout without symlink support turns links into tiny text files holding a path.
        raise ValueError(f'{script} does not look like a Python script. Re-clone this repository '
                         'or check out the latest version.')


def read_marker(path: Path) -> dict | None:
    try:
        data = json.loads((path / MARKER).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _link_language(path: Path) -> str | None:
    """Language of a link made by an older, link-based installer of this project, else None."""
    resolved = path.resolve()
    if resolved == PROJECT_ROOT:
        return 'zh'  # Oldest layout: the whole checkout linked as zotero-daily-papers.
    if resolved == SHARED_SOURCE or resolved == (PROJECT_ROOT / 'skills-en' / SHARED).resolve():
        return 'any'
    if resolved.parent == (PROJECT_ROOT / 'skills-en').resolve():
        return 'en'
    for lang in LANGUAGES:
        if resolved in {p.resolve() for p in sources(lang).values()}:
            return lang
    return None


def install(target: Path, *, lang: str = 'en', dry_run: bool = False,
            update: bool = False) -> list[tuple[str, Path]]:
    """Copy the skills into `target`; returns (action, path) for each entry."""
    planned = sources(lang)
    target = target.expanduser().resolve()
    if target == PROJECT_ROOT or target in PROJECT_ROOT.parents:
        raise ValueError('Target must not be this checkout or contain it')
    if target == SKILLS_DIR or SKILLS_DIR in target.parents:
        raise ValueError('Target must not be inside the skill sources (skills/)')
    if target.exists() and not target.is_dir():
        raise ValueError(f'Target is not a directory: {target}')
    check_bundle(lang)
    actions = []
    # Preflight all entries before changing any of them.
    for name in planned:
        destination = target / name
        if destination.is_symlink():
            previous = _link_language(destination)
            if previous is None:
                raise ValueError(f'Refusing to replace a link not made by this installer: {destination}')
            if previous not in ('any', lang) and not update:
                raise ValueError(f'{destination} is a "{previous}" install. Add --update to switch it to "{lang}".')
            actions.append(('replace-link', destination))
        elif destination.is_dir() and read_marker(destination) is not None:
            previous = read_marker(destination).get('lang')
            if name != SHARED and previous != lang and not update:
                raise ValueError(f'{destination} is a "{previous}" install. Add --update to switch it to "{lang}".')
            actions.append(('update', destination))
        elif destination.exists():
            raise ValueError(f'Refusing to replace existing path: {destination}. If it is a manual copy of '
                             'these skills, back it up and move it away, then install again.')
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
        if destination.is_symlink():
            destination.unlink()  # Only a verified link of this project, never its target.
        elif destination.exists():
            shutil.rmtree(destination)  # Only a verified copy made by this installer.
        staging.rename(destination)
        if not (destination / _required(destination.name)).is_file():
            raise OSError(f'Installed entry is not usable: {destination}')
    return actions


def save_language(lang: str) -> Path:
    """Persist the language in the personal config, keeping every other setting."""
    spec = importlib.util.spec_from_file_location('zdp_config', SHARED_SOURCE / 'zdp_config.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.set_language(lang)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument('--host', choices=tuple(HOST_DIRS),
                       help='Install for this agent: ~/.claude/skills or ~/.agents/skills, '
                            'or inside the project with --project')
    where.add_argument('--target', type=Path, help='Any host skill discovery directory')
    parser.add_argument('--project', type=Path, nargs='?', const=Path('.'), metavar='DIR',
                        help='With --host: install into DIR/.claude/skills or DIR/.agents/skills '
                             '(default DIR: current directory)')
    parser.add_argument('--lang', choices=tuple(LANGUAGES), default='en',
                        help='Skill and note language: en (English, default) or zh (Chinese)')
    parser.add_argument('--copy', action='store_true', help=argparse.SUPPRESS)  # Always copies now.
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--update', action='store_true',
                        help='Switch an existing install to --lang, or migrate a legacy link to this project root')
    args = parser.parse_args()
    if args.project is not None and args.host is None:
        parser.error('--project requires --host')
    try:
        target = args.target or host_target(args.host, args.project)
        actions = install(target, lang=args.lang, dry_run=args.dry_run, update=args.update)
        config = None if args.dry_run else save_language(args.lang)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    prefix = '[dry-run] ' if args.dry_run else ''
    for action, path in actions:
        print(f'{prefix}{action}: {path}')
    print(f'{prefix}language: {args.lang}' + (f' (saved to {config})' if config else ''))


if __name__ == '__main__':
    main()
