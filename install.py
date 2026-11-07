#!/usr/bin/env python3
"""Install the three peer skills and their shared runtime into an agent's skill directory.

`--lang en` (default) installs the English skills (`skills/<name>.en`); `--lang zh` installs the
Chinese skills (`skills/<name>`). Both use the same shared runtime in `skills/_zotero_shared`, and
the chosen language is also saved to the personal config so generated notes match. The skill
sources sit next to `_zotero_shared` so `../_zotero_shared` works even through a link.

By default the skills are symbolic links into this checkout, so updating the checkout updates
every install. `--copy` copies them instead, for systems that cannot create symbolic links
(for example Windows without Developer Mode); rerun the same command after updating.
"""
import argparse
import importlib.util
import json
import os
import shutil
import tempfile
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
COPY_MARKER = '.zdp-install.json'
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


def check_symlinks(target: Path) -> None:
    """Make sure links can be created where they will be installed."""
    base = target
    while not base.exists():
        base = base.parent
    try:
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            os.symlink(SHARED_SOURCE, Path(tmp) / 'probe', target_is_directory=True)
            if not (Path(tmp) / 'probe' / 'config.default.json').is_file():
                raise OSError('the test link does not resolve')
    except OSError as error:
        raise ValueError(f'Cannot create symbolic links in {base} ({error}). On Windows, enable Developer Mode '
                         '(Settings > System > For developers) or run as administrator; '
                         'or rerun with --copy to copy the skills instead.') from None


def _copy_marker(path: Path) -> dict | None:
    try:
        data = json.loads((path / COPY_MARKER).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def install(target: Path, *, lang: str = 'en', dry_run: bool = False, update: bool = False,
            relative: bool = False, copy: bool = False) -> list[tuple[str, Path]]:
    """Install the skills into `target`. `relative` links survive moving a project together with this one."""
    planned = sources(lang)
    target = target.expanduser().resolve()
    if target == PROJECT_ROOT or target in PROJECT_ROOT.parents:
        raise ValueError('Target must not be this checkout or contain it')
    if target == SKILLS_DIR or SKILLS_DIR in target.parents:
        raise ValueError('Target must not be inside the skill sources (skills/)')
    if target.exists() and not target.is_dir():
        raise ValueError(f'Target is not a directory: {target}')
    check_bundle(lang)
    own_links = {path.resolve() for lang_ in LANGUAGES for path in sources(lang_).values()}
    # Links from older versions pointed at skills-en/, which no longer exists.
    old_tree = PROJECT_ROOT / 'skills-en'
    actions = []
    # Preflight all entries before changing any of them.
    for name, source in planned.items():
        destination = target / name
        if destination.is_symlink():
            resolved = destination.resolve()
            legacy = name == 'zotero-daily-papers' and resolved == PROJECT_ROOT
            stale = resolved.parent == old_tree.resolve() and resolved.name == name
            if not copy and resolved == source.resolve():
                actions.append(('unchanged', destination))
            elif stale or (update and (resolved in own_links or legacy)):  # stale links are ours and broken
                actions.append(('relink', destination))
            else:
                raise ValueError(f'Existing link differs: {destination}. Use --update to switch language '
                                 'or install mode, or to migrate a legacy link to this project root.')
        elif destination.is_dir() and _copy_marker(destination) is not None:
            marker = _copy_marker(destination)
            if copy and (marker.get('lang') == lang or name == SHARED or update):
                actions.append(('recopy', destination))
            elif update:
                actions.append(('relink', destination))
            else:
                raise ValueError(f'Existing copy differs: {destination}. Use --update to switch language '
                                 'or install mode.')
        elif destination.exists():
            raise ValueError(f'Refusing to replace existing path: {destination}')
        else:
            actions.append(('copy' if copy else 'link', destination))
    if dry_run:
        return actions
    if not copy and any(action != 'unchanged' for action, _ in actions):
        check_symlinks(target)
    target.mkdir(parents=True, exist_ok=True)
    for action, destination in actions:
        if action == 'unchanged':
            continue
        if destination.is_symlink():
            destination.unlink()  # Only a verified link of this project, never its target.
        elif destination.exists():
            shutil.rmtree(destination)  # Only a verified copy made by this installer.
        source = planned[destination.name]
        if copy:
            shutil.copytree(source, destination, ignore=COPY_IGNORE)
            (destination / COPY_MARKER).write_text(json.dumps({'lang': lang, 'source': str(source)}) + '\n',
                                                   encoding='utf-8')
        else:
            link = Path(os.path.relpath(source, target)) if relative else source
            destination.symlink_to(link, target_is_directory=True)
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
                             '(default DIR: current directory) using relative links')
    parser.add_argument('--lang', choices=tuple(LANGUAGES), default='en',
                        help='Skill and note language: en (English, default) or zh (Chinese)')
    parser.add_argument('--copy', action='store_true',
                        help='Copy the skills instead of linking them (no symlink support needed); '
                             'rerun after updating this checkout')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--update', action='store_true',
                        help='Switch an existing install to --lang or to/from --copy, '
                             'or migrate a legacy link to this project root')
    args = parser.parse_args()
    if args.project is not None and args.host is None:
        parser.error('--project requires --host')
    try:
        target = args.target or host_target(args.host, args.project)
        actions = install(target, lang=args.lang, dry_run=args.dry_run, update=args.update,
                          relative=args.project is not None, copy=args.copy)
        config = None if args.dry_run else save_language(args.lang)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    prefix = '[dry-run] ' if args.dry_run else ''
    for action, path in actions:
        print(f'{prefix}{action}: {path}')
    print(f'{prefix}language: {args.lang}' + (f' (saved to {config})' if config else ''))


if __name__ == '__main__':
    main()
