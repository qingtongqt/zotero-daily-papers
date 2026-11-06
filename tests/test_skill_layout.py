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
        self.assertEqual({p.parent.name for p in (ROOT / 'skills').glob('*/SKILL.md')}, set(installer.SKILLS))
        for entry in (ROOT / 'skills').glob('*/SKILL.md'):
            self.assertIn(f'name: {entry.parent.name}', entry.read_text())
            for link in re.findall(r'\]\(([^)]+)\)', entry.read_text()):
                if ':' not in link and not link.startswith('#'):
                    self.assertTrue((entry.parent / link.split('#')[0]).is_file(), (entry, link))

    def test_install_keeps_shared_runtime_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            installer.install(target)
            for name in (*installer.SKILLS, '_zotero_shared'):
                self.assertEqual((target / name).resolve(), ROOT / 'skills' / name)
            self.assertTrue(all(a == 'unchanged' for a, _ in installer.install(target)))
            self.check_installed_commands(target)

    def test_copied_bundle_runs_without_repository_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'copied-skills'
            shutil.copytree(ROOT / 'skills', target, ignore=shutil.ignore_patterns('__pycache__'))
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
                installer.install(target)
            installer.install(target, update=True)
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
