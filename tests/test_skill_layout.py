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


class LayoutTests(unittest.TestCase):
    def test_three_peer_entries_and_resolvable_references(self):
        self.assertFalse((ROOT / 'SKILL.md').exists())
        for root in installer.LANGUAGES.values():
            self.assertEqual({p.parent.name for p in root.glob('*/SKILL.md')}, set(installer.SKILLS))
            for entry in [*root.glob('*/SKILL.md'), *root.glob('*/references/*.md')]:
                if entry.name == 'SKILL.md':
                    self.assertIn(f'name: {entry.parent.name}', entry.read_text())
                for link in re.findall(r'\]\(([^)]+\.md(?:#[^)]*)?)\)', entry.read_text()):
                    if ':' not in link:
                        self.assertTrue((entry.parent / link.split('#')[0]).is_file(), (entry, link))

    def test_language_trees_mirror_each_other_and_share_code(self):
        zh, en = installer.LANGUAGES['zh'], installer.LANGUAGES['en']
        docs = lambda root: sorted(p.relative_to(root) for p in root.rglob('*.md'))
        self.assertEqual(docs(zh), docs(en))
        self.assertEqual((en / 'zotero-daily-papers/scripts').resolve(), zh / 'zotero-daily-papers/scripts')
        for name in ('zdp_config.py', 'zotero_push.py', 'md2zotero.py', 'config.default.json'):
            self.assertEqual((en / '_zotero_shared' / name).resolve(), zh / '_zotero_shared' / name)
        self.assertNotRegex((en / 'zotero-daily-papers/SKILL.md').read_text(), '[\u4e00-\u9fff]{4}')

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
            for name in (*installer.SKILLS, '_zotero_shared'):
                self.assertEqual((target / name).resolve(), (ROOT / 'skills-en' / name).resolve())
            self.assertTrue(all(a == 'unchanged' for a, _ in installer.install(target, lang='en')))
            self.check_installed_commands(target)
            with self.assertRaises(ValueError):
                installer.install(target, lang='zh')
            self.assertEqual({a for a, _ in installer.install(target, lang='zh', update=True)}, {'relink'})
            self.assertEqual((target / 'zotero-paper-save').resolve(), ROOT / 'skills/zotero-paper-save')
            with self.assertRaises(ValueError):
                installer.install(target, lang='fr')

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
        for lang, root in installer.LANGUAGES.items():
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'copied-skills'
                shutil.copytree(root, target, ignore=shutil.ignore_patterns('__pycache__'))
                self.assertFalse(any(p.is_symlink() for p in target.rglob('*')), lang)
                self.check_installed_commands(target)

    def check_installed_commands(self, target):
        shared = target / '_zotero_shared'
        daily = target / 'zotero-daily-papers/scripts'
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
