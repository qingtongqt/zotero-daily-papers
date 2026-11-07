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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bundle_install', ROOT / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class LayoutTests(unittest.TestCase):
    def test_three_peer_entries_and_resolvable_references(self):
        self.assertFalse((ROOT / 'SKILL.md').exists())
        for lang in installer.LANGUAGES:
            for name, source in installer.sources(lang).items():
                if name == installer.SHARED:
                    continue
                self.assertIn(f'name: {name}', (source / 'SKILL.md').read_text())
                for entry in [source / 'SKILL.md', *source.glob('references/*.md')]:
                    for link in re.findall(r'\]\(([^)]+\.md(?:#[^)]*)?)\)', entry.read_text()):
                        if ':' not in link:
                            self.assertTrue((entry.parent / link.split('#')[0]).is_file(), (entry, link))

    def test_language_trees_mirror_each_other_without_symlinks(self):
        zh, en = installer.sources('zh'), installer.sources('en')
        for name in installer.SKILLS:
            docs = lambda root: sorted(p.relative_to(root) for p in root.rglob('*'))
            self.assertEqual(docs(zh[name]), docs(en[name]))
            self.assertNotRegex((en[name] / 'SKILL.md').read_text(), '[\u4e00-\u9fff]{4}')
        self.assertEqual({p.name for p in installer.SKILLS_DIR.iterdir() if p.name != '__pycache__'},
                         {p.name for p in (*zh.values(), *en.values())})
        # Symlinks stored in git break checkouts with core.symlinks=false (the Windows default).
        self.assertEqual([p for p in ROOT.rglob('*') if p.is_symlink() and '.git' not in p.parts], [])

    def test_install_keeps_shared_runtime_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            installer.install(target, lang='zh')
            for name in (*installer.SKILLS, '_zotero_shared'):
                self.assertEqual((target / name).resolve(), ROOT / 'skills' / name)
            self.assertTrue(all(a == 'unchanged' for a, _ in installer.install(target, lang='zh')))
            self.check_installed_commands(target)

    def test_english_install_and_language_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            installer.install(target, lang='en')
            for name, source in installer.sources('en').items():
                self.assertEqual((target / name).resolve(), source.resolve())
            self.assertTrue(all(a == 'unchanged' for a, _ in installer.install(target, lang='en')))
            self.check_installed_commands(target)
            with self.assertRaises(ValueError):
                installer.install(target, lang='zh')
            actions = {p.name: a for a, p in installer.install(target, lang='zh', update=True)}
            self.assertEqual(actions, {**dict.fromkeys(installer.SKILLS, 'relink'), '_zotero_shared': 'unchanged'})
            self.assertEqual((target / 'zotero-paper-save').resolve(), ROOT / 'skills/zotero-paper-save')
            with self.assertRaises(ValueError):
                installer.install(target, lang='fr')

    def test_host_targets(self):
        self.assertEqual(installer.host_target('claude'), Path.home() / '.claude/skills')
        self.assertEqual(installer.host_target('codex'), Path.home() / '.agents/skills')
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            self.assertEqual(installer.host_target('claude', project), project / '.claude/skills')
            self.assertEqual(installer.host_target('codex', project), project / '.agents/skills')
            with self.assertRaises(ValueError):
                installer.host_target('claude', project / 'missing')
        with self.assertRaises(ValueError):
            installer.host_target('cursor')

    def test_project_install_uses_relative_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Project and this checkout live side by side; moving both keeps the links valid.
            workspace = Path(tmp) / 'workspace'
            checkout = workspace / 'zotero-daily-papers'
            shutil.copytree(ROOT, checkout, symlinks=True,
                            ignore=shutil.ignore_patterns('__pycache__', '.git'))
            project = workspace / 'my-project'
            project.mkdir()
            run = subprocess.run([sys.executable, str(checkout / 'install.py'), '--host', 'claude',
                                  '--project', str(project), '--lang', 'zh'],
                                 env={**os.environ, 'XDG_CONFIG_HOME': tmp}, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            target = project / '.claude/skills'
            for name in (*installer.SKILLS, '_zotero_shared'):
                self.assertFalse(os.path.isabs(os.readlink(target / name)), name)
            moved = Path(tmp) / 'moved'
            workspace.rename(moved)
            for name in (*installer.SKILLS, '_zotero_shared'):
                self.assertEqual((moved / 'my-project/.claude/skills' / name).resolve(),
                                 (moved / 'zotero-daily-papers/skills' / name).resolve())
                self.assertTrue((moved / 'my-project/.claude/skills' / name).is_dir())

    def test_project_install_inside_this_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / 'zotero-daily-papers'
            shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns('__pycache__', '.git'))
            run = subprocess.run([sys.executable, 'install.py', '--host', 'claude', '--project', '--lang', 'zh'],
                                 cwd=checkout, env={**os.environ, 'XDG_CONFIG_HOME': tmp},
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            target = checkout / '.claude/skills'
            for name in (*installer.SKILLS, '_zotero_shared'):
                self.assertFalse(os.path.isabs(os.readlink(target / name)), name)
            self.check_installed_commands(target)
            for bad in (checkout, checkout / 'skills', checkout / 'skills/x', Path(tmp)):
                run = subprocess.run([sys.executable, 'install.py', '--target', str(bad)], cwd=checkout,
                                     env={**os.environ, 'XDG_CONFIG_HOME': tmp}, capture_output=True, text=True)
                self.assertNotEqual(run.returncode, 0, bad)

    def test_copy_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            self.assertEqual({a for a, _ in installer.install(target, lang='zh', copy=True)}, {'copy'})
            self.assertFalse(any(p.is_symlink() for p in target.rglob('*')))
            self.check_installed_commands(target)
            self.assertEqual({a for a, _ in installer.install(target, lang='zh', copy=True)}, {'recopy'})
            with self.assertRaises(ValueError):
                installer.install(target, lang='en', copy=True)
            with self.assertRaises(ValueError):
                installer.install(target, lang='zh')
            installer.install(target, lang='en', copy=True, update=True)
            self.assertNotRegex((target / 'zotero-daily-papers/SKILL.md').read_text(), '[\u4e00-\u9fff]{4}')
            installer.install(target, lang='en', update=True)
            self.assertEqual((target / 'zotero-paper-save').resolve(), ROOT / 'skills/zotero-paper-save.en')
            (target / 'zotero-paper-save').unlink()
            (target / 'zotero-paper-save').mkdir()  # not ours: never replaced
            with self.assertRaises(ValueError):
                installer.install(target, lang='en', copy=True, update=True)

    def test_symlink_preflight_suggests_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            with mock.patch.object(installer.os, 'symlink', side_effect=OSError('privilege not held')):
                with self.assertRaisesRegex(ValueError, '--copy'):
                    installer.install(target)
            self.assertFalse(target.exists())

    def test_broken_checkout_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp) / '_zotero_shared'
            shared.mkdir()
            (shared / 'config.default.json').write_text('{}')
            (shared / 'zdp_config.py').write_text('../../skills/_zotero_shared/zdp_config.py')
            with mock.patch.object(installer, 'SHARED_SOURCE', shared):
                with self.assertRaisesRegex(ValueError, 'does not look like a Python script'):
                    installer.install(Path(tmp) / 'host-skills', dry_run=True)

    def test_old_english_links_are_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            target.mkdir()
            for name in (*installer.SKILLS, installer.SHARED):  # layout of the previous version
                (target / name).symlink_to(ROOT / 'skills-en' / name, target_is_directory=True)
            actions = {a for a, _ in installer.install(target, lang='en')}
            self.assertEqual(actions, {'relink'})
            for name, source in installer.sources('en').items():
                self.assertEqual((target / name).resolve(), source)
            self.check_installed_commands(target)

    def test_project_requires_host(self):
        run = subprocess.run([sys.executable, str(ROOT / 'install.py'), '--target', '/tmp/x', '--project'],
                             capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('--project requires --host', run.stderr)

    def test_cli_saves_language_to_personal_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'zotero-daily-papers/config.json'
            config.parent.mkdir()
            config.write_text(json.dumps({'daily_papers': {'top_n': 4}}))
            env = {**os.environ, 'XDG_CONFIG_HOME': tmp}
            env.pop('ZDP_LANGUAGE', None)
            run = subprocess.run([sys.executable, str(ROOT / 'install.py'), '--target', str(Path(tmp) / 'skills')],
                                 env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(config.read_text()), {'daily_papers': {'top_n': 4}, 'language': 'en'})
            effective = subprocess.run([sys.executable, str(ROOT / 'skills/_zotero_shared/zdp_config.py')],
                                       env=env, capture_output=True, text=True)
            effective = json.loads(effective.stdout)
            self.assertEqual(effective['language'], 'en')
            self.assertEqual(effective['zotero']['pending_collection'], 'Pending')
            run = subprocess.run([sys.executable, str(ROOT / 'install.py'), '--target', str(Path(tmp) / 'other'),
                                  '--dry-run'], env=env, capture_output=True, text=True)
            self.assertEqual(json.loads(config.read_text())['language'], 'en')

    def test_copied_bundle_runs_without_repository_resources(self):
        for lang in installer.LANGUAGES:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'copied-skills'
                for name, source in installer.sources(lang).items():
                    shutil.copytree(source, target / name, ignore=shutil.ignore_patterns('__pycache__'))
                self.check_installed_commands(target)

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
            for link in re.findall(r'\]\(([^)]+)\)', entry.read_text()):
                if ':' not in link and not link.startswith('#'):
                    self.assertTrue((entry.parent / link.split('#')[0]).is_file(), (entry, link))

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            actions = installer.install(target, dry_run=True)
            self.assertEqual(len(actions), 4)
            self.assertFalse(target.exists())

    def test_conflict_does_not_partially_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / 'zotero-paper-reader').mkdir()
            with self.assertRaises(ValueError):
                installer.install(target, update=True)
            self.assertFalse((target / 'zotero-daily-papers').exists())
            self.assertFalse((target / 'zotero-paper-save').exists())

    def test_legacy_link_requires_explicit_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            legacy = target / 'zotero-daily-papers'
            legacy.symlink_to(ROOT, target_is_directory=True)
            with self.assertRaises(ValueError):
                installer.install(target, lang='zh')
            installer.install(target, lang='zh', update=True)
            self.assertEqual(legacy.resolve(), ROOT / 'skills/zotero-daily-papers')
            self.assertTrue((target / '_zotero_shared/config.default.json').is_file())

    def test_unknown_link_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            target.mkdir()
            foreign = Path(tmp) / 'foreign'
            foreign.mkdir()
            link = target / 'zotero-daily-papers'
            link.symlink_to(foreign, target_is_directory=True)
            with self.assertRaises(ValueError):
                installer.install(target, update=True)
            self.assertEqual(link.resolve(), foreign.resolve())


if __name__ == '__main__':
    unittest.main()
