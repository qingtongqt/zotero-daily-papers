#!/usr/bin/env python3
"""Register the three peer skills as links, including their shared runtime.

`--lang en` (default) links the English skills in `skills-en/`; `--lang zh` links the Chinese
skills in `skills/`. Both trees share the same scripts, and the chosen language is also
saved to the personal config so generated notes and default collection names match.
"""
import argparse
import importlib.util
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SKILLS = ('zotero-daily-papers', 'zotero-paper-save', 'zotero-paper-reader')
LANGUAGES = {'zh': PROJECT_ROOT / 'skills', 'en': PROJECT_ROOT / 'skills-en'}
# Skill discovery directory per host, relative to the home directory (user scope)
# or to the project directory (project scope).
HOST_DIRS = {'claude': Path('.claude/skills'), 'codex': Path('.agents/skills')}


def host_target(host: str, project: Path | None = None) -> Path:
    """User scope without `project`; project scope inside `project` otherwise."""
    if host not in HOST_DIRS:
        raise ValueError(f'Unsupported host: {host}. Choose one of: {", ".join(HOST_DIRS)}')
    base = Path.home() if project is None else project.expanduser().resolve()
    if project is not None and not base.is_dir():
        raise ValueError(f'Project directory does not exist: {base}')
    return base / HOST_DIRS[host]


def skill_root(lang: str) -> Path:
    if lang not in LANGUAGES:
        raise ValueError(f'Unsupported language: {lang}. Choose one of: {", ".join(LANGUAGES)}')
    return LANGUAGES[lang]


def install(target: Path, *, lang: str = 'en', dry_run: bool = False,
            update: bool = False, relative: bool = False) -> list[tuple[str, Path]]:
    """Link the skills into `target`. `relative` links survive moving a project together with this one."""
    root = skill_root(lang)
    target = target.expanduser().resolve()
    if target == PROJECT_ROOT or PROJECT_ROOT in target.parents or target in PROJECT_ROOT.parents:
        raise ValueError('Target must be outside the project and must not contain it')
    if target.exists() and not target.is_dir():
        raise ValueError(f'Target is not a directory: {target}')
    actions = []
    # Preflight all entries before changing any of them.
    for name in (*SKILLS, '_zotero_shared'):
        source = root / name
        destination = target / name
        required = 'config.default.json' if name == '_zotero_shared' else 'SKILL.md'
        if not (source / required).is_file():
            raise ValueError(f'Missing bundle resource: {source / required}')
        if destination.is_symlink():
            resolved = destination.resolve()
            own = {(other / name).resolve() for other in LANGUAGES.values()}
            if resolved == source.resolve():
                actions.append(('unchanged', destination))
            elif update and (resolved in own or (name == 'zotero-daily-papers' and resolved == PROJECT_ROOT)):
                # Switching language, or migrating the legacy link to the project root.
                actions.append(('relink', destination))
            else:
                raise ValueError(f'Existing link differs: {destination}. Use --update to switch language '
                                 'or migrate a legacy link to this project root.')
        elif destination.exists():
            raise ValueError(f'Refusing to replace existing path: {destination}')
        else:
            actions.append(('link', destination))
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
        for action, destination in actions:
            if action == 'unchanged':
                continue
            if action == 'relink':
                destination.unlink()  # Only a verified link of this project, never its target.
            source = root / destination.name
            if relative:
                source = Path(os.path.relpath(source, target))
            destination.symlink_to(source, target_is_directory=True)
    return actions


def save_language(lang: str) -> Path:
    """Persist the language in the personal config, keeping every other setting."""
    spec = importlib.util.spec_from_file_location('zdp_config', PROJECT_ROOT / 'skills/_zotero_shared/zdp_config.py')
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
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--update', action='store_true',
                        help='Switch an existing install to --lang, or migrate a legacy link to this project root')
    args = parser.parse_args()
    if args.project is not None and args.host is None:
        parser.error('--project requires --host')
    try:
        target = args.target or host_target(args.host, args.project)
        actions = install(target, lang=args.lang, dry_run=args.dry_run, update=args.update,
                          relative=args.project is not None)
        config = None if args.dry_run else save_language(args.lang)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    prefix = '[dry-run] ' if args.dry_run else ''
    for action, path in actions:
        print(f'{prefix}{action}: {path}')
    print(f'{prefix}language: {args.lang}' + (f' (saved to {config})' if config else ''))


if __name__ == '__main__':
    main()
