#!/usr/bin/env python3
"""Register the three peer skills as links, including their shared runtime."""
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SKILLS = ('zotero-daily-papers', 'zotero-paper-save', 'zotero-paper-reader')


def install(target: Path, *, dry_run: bool = False, update: bool = False) -> list[tuple[str, Path]]:
    target = target.expanduser().resolve()
    if target == PROJECT_ROOT or PROJECT_ROOT in target.parents or target in PROJECT_ROOT.parents:
        raise ValueError('Target must be outside the project and must not contain it')
    if target.exists() and not target.is_dir():
        raise ValueError(f'Target is not a directory: {target}')
    actions = []
    # Preflight all entries before changing any of them.
    for name in (*SKILLS, '_zotero_shared'):
        source = PROJECT_ROOT / 'skills' / name
        destination = target / name
        required = 'config.default.json' if name == '_zotero_shared' else 'SKILL.md'
        if not (source / required).is_file():
            raise ValueError(f'Missing bundle resource: {source / required}')
        if destination.is_symlink():
            resolved = destination.resolve()
            if resolved == source:
                actions.append(('unchanged', destination))
            elif name == 'zotero-daily-papers' and resolved == PROJECT_ROOT and update:
                actions.append(('migrate', destination))
            else:
                raise ValueError(f'Existing link differs: {destination}. '
                                 'Only a legacy link to this project root can be migrated with --update.')
        elif destination.exists():
            raise ValueError(f'Refusing to replace existing path: {destination}')
        else:
            actions.append(('link', destination))
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
        for action, destination in actions:
            if action == 'unchanged':
                continue
            if action == 'migrate':
                destination.unlink()  # Only the verified legacy link, never its target.
            destination.symlink_to(PROJECT_ROOT / 'skills' / destination.name, target_is_directory=True)
    return actions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True, help='Host skill discovery directory')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--update', action='store_true', help='Migrate a legacy link to this project root')
    args = parser.parse_args()
    try:
        actions = install(args.target, dry_run=args.dry_run, update=args.update)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    for action, path in actions:
        print(f'{"[dry-run] " if args.dry_run else ""}{action}: {path}')


if __name__ == '__main__':
    main()
