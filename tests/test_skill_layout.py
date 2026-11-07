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


def can_symlink() -> bool:
    """Windows without Developer Mode or admin rights cannot create symbolic links."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.symlink(tmp, Path(tmp) / 'probe', target_is_directory=True)
            return True
        except OSError:
            return False


needs_symlinks = unittest.skipUnless(can_symlink(), 'symbolic links are not available on this system')


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
            docs = lambda root: sorted(p.relative_to(root) for p in root.rglob('*'))
            self.assertEqual(docs(zh[name]), docs(en[name]))
            self.assertNotRegex(read(en[name] / 'SKILL.md'), '[一-鿿]{4}')
        self.assertEqual({p.name for p in installer.SKILLS_DIR.iterdir() if p.name != '__pycache__'},
                         {p.name for p in (*zh.values(), *en.values())})
        # Symlinks stored in git break checkouts with core.symlinks=false (the Windows default).
        self.assertEqual([p for p in ROOT.rglob('*') if p.is_symlink() and '.git' not in p.parts], [])


class InstallTests(unittest.TestCase):
    def assert_copied(self, target: Path, lang: str):
        for name, source in installer.sources(lang).items():
            destination = target / name
            self.assertFalse(destination.is_symlink(), name)
            self.assertEqual(installer.read_marker(destination), {'lang': lang})
            self.assertEqual(read(destination / installer._required(name)), read(source / installer._required(name)))
        self.assertFalse(any(p.is_symlink() for p in target.rglob('*')))

    def test_copy_install_is_idempotent_and_runs_without_the_checkout(self):
        for lang in installer.LANGUAGES:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'host-skills'
                self.assertEqual({a for a, _ in installer.install(target, lang=lang)}, {'copy'})
                self.assert_copied(target, lang)
                (target / '_zotero_shared/stale.txt').write_text('old')
                self.assertEqual({a for a, _ in installer.install(target, lang=lang)}, {'update'})
                self.assertFalse((target / '_zotero_shared/stale.txt').exists())
                self.assertFalse(any(p.name.endswith('.zdp-new') for p in target.iterdir()))
                self.check_installed_commands(target)

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

    def test_manual_copy_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            shutil.copytree(installer.SKILLS_DIR / 'zotero-paper-save', target / 'zotero-paper-save')
            with self.assertRaisesRegex(ValueError, 'back it up and move it away'):
                installer.install(target, update=True)
            # Preflight failed, so nothing else was installed.
            self.assertEqual([p.name for p in target.iterdir()], ['zotero-paper-save'])

    def test_targets_inside_the_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / 'zotero-daily-papers'
            shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns('__pycache__', '.git'))
            env = {**os.environ, 'XDG_CONFIG_HOME': tmp}
            run = subprocess.run([sys.executable, 'install.py', '--host', 'claude', '--project', '--lang', 'zh'],
                                 cwd=checkout, env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.check_installed_commands(checkout / '.claude/skills')
            for bad in (checkout, checkout / 'skills', checkout / 'skills/x', Path(tmp)):
                run = subprocess.run([sys.executable, 'install.py', '--target', str(bad)], cwd=checkout,
                                     env=env, capture_output=True, text=True)
                self.assertNotEqual(run.returncode, 0, bad)

    def test_project_install_survives_removing_the_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / 'zotero-daily-papers'
            shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns('__pycache__', '.git'))
            project = Path(tmp) / 'my-project'
            project.mkdir()
            run = subprocess.run([sys.executable, str(checkout / 'install.py'), '--host', 'codex',
                                  '--project', str(project)],
                                 env={**os.environ, 'XDG_CONFIG_HOME': tmp}, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            shutil.rmtree(checkout)
            self.check_installed_commands(project / '.agents/skills')

    def test_broken_checkout_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp) / '_zotero_shared'
            shared.mkdir()
            (shared / 'config.default.json').write_text('{}')
            (shared / 'zdp_config.py').write_text('../../skills/_zotero_shared/zdp_config.py')
            with mock.patch.object(installer, 'SHARED_SOURCE', shared):
                with self.assertRaisesRegex(ValueError, 'does not look like a Python script'):
                    installer.install(Path(tmp) / 'host-skills', dry_run=True)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'host-skills'
            actions = installer.install(target, dry_run=True)
            self.assertEqual(len(actions), 4)
            self.assertFalse(target.exists())

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

    def test_project_requires_host(self):
        run = subprocess.run([sys.executable, str(ROOT / 'install.py'), '--target', 'x', '--project'],
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
                                  '--lang', 'zh', '--dry-run'], env=env, capture_output=True, text=True)
            self.assertEqual(json.loads(config.read_text())['language'], 'en')

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


@needs_symlinks
class LinkMigrationTests(unittest.TestCase):
    """Older versions installed links; reinstalling replaces those links with copies."""

    def test_links_from_older_versions_become_copies(self):
        layouts = {
            'en': {name: ROOT / 'skills-en' / name for name in (*installer.SKILLS, installer.SHARED)},
            'zh': installer.sources('zh'),
        }
        for lang, links in layouts.items():
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'host-skills'
                target.mkdir()
                for name, source in links.items():
                    (target / name).symlink_to(source, target_is_directory=True)
                self.assertEqual({a for a, _ in installer.install(target, lang=lang)}, {'replace-link'})
                InstallTests.assert_copied(self, target, lang)
                # Only the links were removed, never what they pointed to.
                self.assertTrue((installer.SHARED_SOURCE / 'config.default.json').is_file())

    def test_language_switch_of_a_link_install_requires_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            for name, source in installer.sources('zh').items():
                (target / name).symlink_to(source, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, '--update'):
                installer.install(target, lang='en')
            installer.install(target, lang='en', update=True)
            InstallTests.assert_copied(self, target, 'en')

    def test_legacy_link_to_checkout_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / 'zotero-daily-papers').symlink_to(ROOT, target_is_directory=True)
            installer.install(target, lang='zh')
            self.assertFalse((target / 'zotero-daily-papers').is_symlink())
            self.assertTrue((ROOT / 'install.py').is_file())  # The checkout itself is untouched.

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
