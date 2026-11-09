import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bundle_install', ROOT / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


# Local files that are not part of the skills: Python caches and macOS Finder metadata.
IGNORED = {'__pycache__', '.DS_Store'}


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


class LayoutTests(unittest.TestCase):
    def test_three_peer_entries_and_resolvable_references(self):
        self.assertFalse((ROOT / 'SKILL.md').exists())
        for lang in installer.LANGUAGES:
            for name, source in installer.sources(lang).items():
                if name == installer.SHARED:
                    continue
                self.assertIn(f'name: {name}', read(source / 'SKILL.md'))
                for entry in [source / 'SKILL.md', *source.glob('references/*.md')]:
                    for link in re.findall(r'\]\(([^)]+\.md(?:#[^)]*)?)\)', read(entry)):
                        if ':' not in link:
                            self.assertTrue((entry.parent / link.split('#')[0]).is_file(), (entry, link))

    def test_language_trees_mirror_each_other_without_symlinks(self):
        zh, en = installer.sources('zh'), installer.sources('en')
        for name in installer.SKILLS:
            docs = lambda root: sorted(p.relative_to(root) for p in root.rglob('*') if p.name not in IGNORED)
            self.assertEqual(docs(zh[name]), docs(en[name]))
            self.assertNotRegex(read(en[name] / 'SKILL.md'), '[一-鿿]{4}')
        self.assertEqual({p.name for p in installer.SKILLS_DIR.iterdir() if p.name not in IGNORED},
                         {p.name for p in (*zh.values(), *en.values())})
        # Symlinks stored in git break checkouts with core.symlinks=false (the Windows default).
        self.assertEqual([p for p in ROOT.rglob('*') if p.is_symlink() and '.git' not in p.parts], [])


class InstallTests(unittest.TestCase):
    def assert_copied(self, target: Path, lang: str):
        for name, source in installer.sources(lang).items():
            destination = target / name
            required = 'config.default.json' if name == installer.SHARED else 'SKILL.md'
            self.assertEqual(installer.read_marker(destination), {'lang': lang})
            self.assertEqual(read(destination / required), read(source / required))
        self.assertFalse(any(p.is_symlink() for p in target.rglob('*')))

    def cli(self, *args, cwd=None, home=None):
        env = {**os.environ, 'XDG_CONFIG_HOME': str(home or cwd or ROOT)}
        if home:
            env.update(HOME=str(home), USERPROFILE=str(home))
        env.pop('ZDP_LANGUAGE', None)
        return subprocess.run([sys.executable, str(ROOT / 'install.py'), *args], cwd=cwd, env=env,
                              capture_output=True, text=True)

    def test_copy_install_is_idempotent_and_keeps_its_config(self):
        for lang in installer.LANGUAGES:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'host-skills'
                self.assertEqual({a for a, _ in installer.install(target, lang=lang)}, {'copy'})
                self.assert_copied(target, lang)
                (target / '_zotero_shared/config.json').write_text('{"daily_papers": {"top_n": 3}}')
                (target / '_zotero_shared/stale.txt').write_text('old')
                self.assertEqual({a for a, _ in installer.install(target, lang=lang)}, {'update'})
                self.assertFalse((target / '_zotero_shared/stale.txt').exists())
                self.assertEqual(json.loads(read(target / '_zotero_shared/config.json')), {'daily_papers': {'top_n': 3}})
                self.assertFalse(any(p.name.endswith('.zdp-new') for p in target.iterdir()))
                self.check_installed_commands(target)

    def test_interrupted_update_keeps_the_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            installer.install(target, lang='zh')
            config = target / '_zotero_shared/config.json'
            config.write_text('{"daily_papers": {"top_n": 3}}')
            real_rename = Path.rename

            def failing_rename(path, destination):
                if path.name == '_zotero_shared.zdp-new':
                    raise OSError('interrupted')
                return real_rename(path, destination)

            from unittest import mock
            with mock.patch.object(Path, 'rename', failing_rename):
                with self.assertRaises(OSError):
                    installer.install(target, lang='zh')
            installer.install(target, lang='zh')  # The rerun recovers the previous copy first.
            self.assertEqual(json.loads(read(config)), {'daily_papers': {'top_n': 3}})
            self.assertEqual(sorted(p.name for p in target.iterdir()), sorted(installer.sources('zh')))

    def test_language_switch_requires_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            installer.install(target, lang='en')
            with self.assertRaisesRegex(ValueError, '--update'):
                installer.install(target, lang='zh')
            self.assert_copied(target, 'en')
            installer.install(target, lang='zh', update=True)
            self.assert_copied(target, 'zh')
            with self.assertRaises(ValueError):
                installer.install(target, lang='fr')

    def test_paths_not_installed_by_the_script_are_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            shutil.copytree(installer.SKILLS_DIR / 'zotero-paper-save', target / 'zotero-paper-save')
            with self.assertRaisesRegex(ValueError, 'Refusing to replace'):
                installer.install(target, update=True)
            # The check failed before anything was copied.
            self.assertEqual([p.name for p in target.iterdir()], ['zotero-paper-save'])

    def test_cli_project_install_with_its_own_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'my-project'
            project.mkdir()
            run = self.cli('--host', 'codex', '--project', str(project), '--lang', 'zh', cwd=tmp)
            self.assertEqual(run.returncode, 0, run.stderr)
            target = project / '.agents/skills'
            self.assert_copied(target, 'zh')
            self.assertEqual(json.loads(read(target / '_zotero_shared/config.json')), {'language': 'zh'})
            # --project without a directory means the current directory.
            run = self.cli('--host', 'claude', '--project', cwd=project)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(read(project / '.claude/skills/_zotero_shared/config.json')),
                             {'language': 'en'})
            self.assertEqual(json.loads(read(target / '_zotero_shared/config.json')), {'language': 'zh'})
            self.assertFalse((ROOT / 'skills/_zotero_shared/config.json').exists())
            self.check_installed_commands(target)

    def test_cli_user_level_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.cli('--host', 'claude', home=Path(tmp))
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assert_copied(Path(tmp) / '.claude/skills', 'en')

    def test_install_into_this_checkout_and_survives_its_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / 'zotero-daily-papers'
            shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns('__pycache__', '.git'))
            run = subprocess.run([sys.executable, 'install.py', '--host', 'claude', '--project'], cwd=checkout,
                                 env={**os.environ, 'XDG_CONFIG_HOME': tmp}, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            installed = Path(tmp) / 'installed'
            shutil.move(checkout / '.claude/skills', installed)
            shutil.rmtree(checkout)
            self.check_installed_commands(installed)
            with self.assertRaises(ValueError):
                installer.install(installer.SKILLS_DIR / 'x', dry_run=True)

    def test_cli_rejects_old_and_invalid_options(self):
        for args in (['--target', 'x'], ['--project'], ['--host', 'cursor']):
            run = self.cli(*args)
            self.assertNotEqual(run.returncode, 0, args)
        with tempfile.TemporaryDirectory() as tmp:
            run = self.cli('--host', 'claude', '--project', str(Path(tmp) / 'missing'))
            self.assertNotEqual(run.returncode, 0)
            run = self.cli('--host', 'claude', '--project', tmp, '--dry-run')
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse((Path(tmp) / '.claude').exists())

    def test_host_targets(self):
        self.assertEqual(installer.host_target('claude'), Path.home() / '.claude/skills')
        self.assertEqual(installer.host_target('codex'), Path.home() / '.agents/skills')
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            self.assertEqual(installer.host_target('claude', project), project / '.claude/skills')
            self.assertEqual(installer.host_target('codex', project), project / '.agents/skills')

    def check_installed_commands(self, target):
        shared = target / '_zotero_shared'
        daily = shared / 'scripts'
        def run(script, *args, stdin=''):
            result = subprocess.run([sys.executable, str(script), *args], cwd=target.parent,
                                    env={**os.environ, 'PYTHONPATH': '', 'XDG_CONFIG_HOME': str(target.parent),
                                         'ZDP_TEMP_DIR': str(target.parent)},
                                    input=stdin, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        self.assertIn('daily_papers', json.loads(run(shared / 'zdp_config.py')))
        run(shared / 'zotero_push.py', '--help')
        run(daily / 'fetch_and_score.py', '--help')
        run(daily / 'agent_ranker.py', '--help')
        self.assertEqual(json.loads(run(daily / 'enrich_papers.py', stdin='[]')), [])
        for entry in target.glob('*/SKILL.md'):
            for link in re.findall(r'\]\(([^)]+)\)', read(entry)):
                if ':' not in link and not link.startswith('#'):
                    self.assertTrue((entry.parent / link.split('#')[0]).is_file(), (entry, link))


if __name__ == '__main__':
    unittest.main()
