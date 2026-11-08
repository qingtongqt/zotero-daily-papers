from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_zotero_shared'))
import zotero_push as zp
from zdp_config import classification_mode


class MemoryZotero:
    dry_run = False
    base = 'memory://library'
    cache_identity = base

    def __init__(self, fail_digest=False):
        self.items = {}
        self.collections = {'CUSTOM01': {'key': 'CUSTOM01', 'name': 'Existing topic', 'parentCollection': False}}
        self.events = []
        self.fail_digest = fail_digest

    def create(self, objects, path='/items'):
        keys = []
        for obj in objects:
            if self.fail_digest and obj.get('itemType') == 'note' and not obj.get('parentItem'):
                raise zp.ZoteroError('digest rejected')
            key = f'KEY{len(self.items) + len(self.collections):05}'
            self.events.append(('create', copy.deepcopy(obj)))
            if path == '/collections':
                self.collections[key] = {'key': key, **copy.deepcopy(obj)}
            else:
                self.items[key] = {'key': key, 'version': 1, 'data': copy.deepcopy(obj)}
            keys.append(key)
        return keys

    def get_all(self, path, params=None):
        if path == '/collections':
            return [{'key': k, 'data': copy.deepcopy(v)} for k, v in self.collections.items()]
        if path.endswith('/children'):
            key = path.split('/')[2]
            return [copy.deepcopy(v) for v in self.items.values() if v['data'].get('parentItem') == key]
        return self.get(path, params)

    def get(self, path, params=None):
        params = params or {}
        rows = list(self.items.values())
        if params.get('q'):
            rows = [r for r in rows if zp.item_matches(r['data'], params['q'])]
        if params.get('tag'):
            rows = [r for r in rows if set(params['tag']) <= {t['tag'] for t in r['data'].get('tags', [])}]
        if params.get('itemType'):
            rows = [r for r in rows if r['data']['itemType'] == params['itemType']]
        return copy.deepcopy(rows)

    def item(self, key):
        return copy.deepcopy(self.items.get(key))

    def patch(self, key, version, fields):
        if self.items[key]['version'] != version:
            raise zp.ZoteroError('version conflict')
        self.items[key]['version'] += 1
        self.items[key]['data'].update(copy.deepcopy(fields))
        self.events.append(('patch', copy.deepcopy(fields)))

    def select_link(self, key):
        return f'zotero://select/library/items/{key}'


class NoteCacheTests(unittest.TestCase):
    def test_cache_permission_error_after_note_creation_still_returns_note_key(self):
        zot = MemoryZotero()
        parent = zot.create([{'itemType': 'preprint', 'title': 'Paper'}])[0]
        stdout, stderr = StringIO(), StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            markdown = Path(tmp) / 'note.md'
            markdown.write_text('# Reading note')
            args = Namespace(item=parent, arxiv=None, markdown=str(markdown),
                             kind='full-note', tags=None, replace=False)
            with patch.object(zp, 'cache_path', return_value=Path(tmp) / 'cache.json'), \
                    patch.object(Path, 'write_text', side_effect=PermissionError('sandbox denied')), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                zp.cmd_add_note(zot, {}, args)
        result = json.loads(stdout.getvalue())
        self.assertEqual(zot.item(result['note_key'])['data']['parentItem'], parent)
        self.assertIn('sandbox denied', stderr.getvalue())


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.tmp)
        for name, value in [('load_cache', {}), ('load_history', []), ('save_cache', None), ('save_history', None)]:
            self.enterContext(patch.object(zp, name, return_value=value))
        self.enterContext(patch.object(zp, 'fetch_arxiv_meta_batch', return_value={}))
        self.payload = {'date': '2026-11-04', 'digest_markdown': '# Daily\n[VCD](zotero:2311.16922)',
                        'papers': [{'arxiv_id': '2311.16922', 'tier': 'must', 'review_markdown': '# Review'}]}
        self.enriched = [{'arxiv_id': '2311.16922', 'title': 'VCD', 'abstract': 'Visual contrastive decoding'}]
        (self.path / 'e.json').write_text(json.dumps(self.enriched))
        self.args = Namespace(payload=str(self.path / 'p.json'), enriched=str(self.path / 'e.json'),
                              output=str(self.path / 'result.json'))
        self.cfg = {'root_collection': 'Daily Papers',
                    'attach_pdf': 'none', 'max_embedded_images_per_note': 0}

    def run_pipeline(self, zot):
        (self.path / 'p.json').write_text(json.dumps(self.payload))
        zp.cmd_push_daily(zot, self.cfg, self.args)
        return json.loads((self.path / 'result.json').read_text())

    def run_pipeline_with(self, zot, **flags):
        old = self.args
        self.args = Namespace(**{**vars(old), **flags})
        try:
            return self.run_pipeline(zot)
        finally:
            self.args = old

    def read_result(self):
        return json.loads((self.path / 'result.json').read_text())

    def test_digest_precedes_paper_default_pending_and_rerun_is_idempotent(self):
        zot = MemoryZotero()
        # Pending mode ignores stale classification decisions.
        self.payload['papers'][0]['collection_keys'] = ['STALEKEY']
        result = self.run_pipeline(zot)
        creates = [obj for event, obj in zot.events if event == 'create' and 'itemType' in obj]
        self.assertEqual([o['itemType'] for o in creates], ['note', 'preprint', 'note'])
        self.assertIn('https://arxiv.org/abs/2311.16922', creates[0]['note'])
        key = result['papers']['2311.16922']['item_key']
        pending = next(k for k, c in zot.collections.items() if c['name'] == 'Pending')
        self.assertEqual(zot.items[key]['data']['collections'], [pending])
        digest = zot.items[result['digest_note_key']]['data']['note']
        self.assertIn(zot.select_link(key), digest)
        self.assertEqual(result['stage'], 'papers_saved')
        for item in zot.items.values():
            self.assertFalse(any(t['tag'] == 'dp' or t['tag'].startswith('dp/')
                                 for t in item['data'].get('tags', [])))
        self.assertEqual(result['reading_queue'], [{'arxiv_id': '2311.16922', 'item_key': key}])
        count = len(zot.items)
        repeated = self.run_pipeline(zot)
        self.assertEqual(len(zot.items), count)
        # The task record is cumulative: the rerun keeps the first run's "created".
        self.assertTrue(repeated['papers']['2311.16922']['created'])
        fresh = self.run_pipeline_with(zot, fresh=True)
        self.assertEqual(len(zot.items), count)
        self.assertFalse(fresh['papers']['2311.16922']['created'])
        self.assertEqual(len(fresh['runs']), 1)

    def test_digest_lives_directly_in_daily_papers(self):
        zot = MemoryZotero()
        result = self.run_pipeline(zot)
        root = next(k for k, c in zot.collections.items() if c['name'] == 'Daily Papers')
        self.assertEqual(zot.items[result['digest_note_key']]['data']['collections'], [root])
        self.assertFalse([c for c in zot.collections.values()
                          if c.get('parentCollection') == root])
        count = len(zot.items)
        again = self.run_pipeline(zot)                       # same-day rerun reuses the overview
        self.assertEqual(again['digest_note_key'], result['digest_note_key'])
        self.assertEqual(len(zot.items), count)

    def test_digest_failure_prevents_paper_and_pending_creation(self):
        zot = MemoryZotero(fail_digest=True)
        with self.assertRaises(zp.ZoteroError):
            self.run_pipeline(zot)
        self.assertEqual(zot.items, {})
        self.assertNotIn('Pending', [c['name'] for c in zot.collections.values()])
        saved = json.loads((self.path / 'result.json').read_text())
        self.assertEqual(saved['stage'], 'digest_failed')

    def test_existing_mode_requires_selection_before_any_write(self):
        self.cfg['classification_mode'] = 'existing'
        zot = MemoryZotero()
        with self.assertRaises(zp.ZoteroError):
            self.run_pipeline(zot)
        self.assertEqual(zot.events, [])
        self.payload['papers'][0]['collection_keys'] = ['CUSTOM01']
        result = self.run_pipeline(zot)
        key = result['papers']['2311.16922']['item_key']
        self.assertEqual(zot.items[key]['data']['collections'], ['CUSTOM01'])
        self.assertNotIn('Pending', [c['name'] for c in zot.collections.values()])

    def test_existing_no_match_uses_pending(self):
        self.cfg['classification_mode'] = 'existing'
        self.payload['papers'][0]['collection_keys'] = []
        zot = MemoryZotero()
        self.run_pipeline(zot)
        self.assertIn('Pending', [c['name'] for c in zot.collections.values()])

    def test_failed_paper_not_in_reading_queue_or_history(self):
        zot = MemoryZotero()
        with patch.object(zp, 'ensure_paper', side_effect=zp.ZoteroError('paper rejected')), \
                patch.object(zp, 'save_history') as history:
            with self.assertRaises(SystemExit):
                self.run_pipeline(zot)
        result = json.loads((self.path / 'result.json').read_text())
        self.assertEqual(result['stage'], 'papers_partial')
        self.assertEqual(result['reading_queue'], [])
        history.assert_called_once_with([])
        self.assertEqual(len(zot.items), 1)  # overview remains

    def add_papers(self, *ids):
        for aid in ids:
            self.payload['papers'].append({'arxiv_id': aid, 'tier': 'worth', 'review_markdown': f'# Review {aid}'})
            self.enriched.append({'arxiv_id': aid, 'title': f'Paper {aid}'})
        (self.path / 'e.json').write_text(json.dumps(self.enriched))

    def test_partial_failure_resumes_only_unfinished_papers_and_keeps_created(self):
        zot = MemoryZotero()
        existing = zot.create([zp.item_payload({'arxiv_id': '2401.00003', 'title': 'Old'}, ['CUSTOM01'], [])])[0]
        self.add_papers('2401.00002', '2401.00003')
        real_ensure = zp.ensure_paper

        def flaky(zot_, meta, *a, **kw):
            if meta['arxiv_id'] == '2401.00002':
                raise zp.ZoteroError('GET /items -> HTTP 500')
            return real_ensure(zot_, meta, *a, **kw)

        with patch.object(zp, 'ensure_paper', side_effect=flaky), self.assertRaises(SystemExit):
            self.run_pipeline(zot)
        first = self.read_result()
        self.assertEqual(first['stage'], 'papers_partial')
        self.assertEqual(first['runs'][0]['processed'], ['2311.16922', '2401.00002', '2401.00003'])
        self.assertIn('HTTP 500', first['papers']['2401.00002']['error'])

        events = len(zot.events)
        with patch.object(zp, 'find_item', wraps=zp.find_item) as finds, \
                patch.object(zp, 'ensure_paper', wraps=real_ensure) as ensures:
            second = self.run_pipeline(zot)
        # Only the failed paper is redone; finished papers and dedup are not repeated.
        self.assertEqual([c.args[1]['arxiv_id'] for c in ensures.call_args_list], ['2401.00002'])
        self.assertEqual([c.args[1] for c in finds.call_args_list], ['2401.00002'])
        self.assertEqual(second['stage'], 'papers_saved')
        self.assertEqual(second['errors'], [])
        self.assertEqual(second['runs'][1]['processed'], ['2401.00002'])
        self.assertEqual(second['runs'][1]['skipped'], ['2311.16922', '2401.00003'])
        self.assertTrue(second['runs'][1]['resumed'])
        papers = second['papers']
        self.assertTrue(papers['2311.16922']['created'])
        self.assertEqual(papers['2311.16922']['created_in_run'], 1)
        self.assertTrue(papers['2401.00002']['created'])
        self.assertEqual(papers['2401.00002']['created_in_run'], 2)
        self.assertEqual(papers['2401.00002']['attempts'], 2)
        self.assertNotIn('error', papers['2401.00002'])
        self.assertFalse(papers['2401.00003']['created'])
        self.assertEqual(papers['2401.00003']['existing_item_key'], existing)
        self.assertEqual(second['summary'], {'papers': 3, 'saved': 3, 'created': 2, 'existing': 1,
                                             'failed': 0, 'runs': 2})
        self.assertGreater(len(zot.events), events)
        # A third run has nothing left to do and writes nothing.
        events = len(zot.events)
        third = self.run_pipeline(zot)
        self.assertEqual(len(zot.events), events)
        self.assertEqual(third['runs'][2]['processed'], [])
        self.assertEqual(third['summary']['created'], 2)

    def test_dedup_read_failure_is_recorded_and_resumed(self):
        zot = MemoryZotero()
        self.add_papers('2401.00002')
        real_find = zp.find_item

        def flaky(zot_, aid, cache):
            if aid == '2401.00002':
                raise zp.ZoteroError('GET /items -> HTTP 500')
            return real_find(zot_, aid, cache)

        with patch.object(zp, 'find_item', side_effect=flaky), self.assertRaises(zp.ZoteroError):
            self.run_pipeline(zot)
        first = self.read_result()
        self.assertEqual(first['stage'], 'dedup_failed')
        self.assertIn('dedup: GET /items -> HTTP 500', first['errors'])
        self.assertIn('existing_item_key', first['papers']['2311.16922'])
        self.assertEqual(zot.events, [])                 # nothing written before dedup finished
        with patch.object(zp, 'find_item', wraps=real_find) as finds:
            second = self.run_pipeline(zot)
        self.assertEqual([c.args[1] for c in finds.call_args_list if c.args[1] == '2311.16922'], ['2311.16922'])
        self.assertEqual(second['stage'], 'papers_saved')
        self.assertEqual(second['summary']['created'], 2)
        self.assertEqual([r['stage'] for r in second['runs']], ['dedup_failed', 'papers_saved'])

    def test_changed_review_is_rewritten_on_resume(self):
        zot = MemoryZotero()
        first = self.run_pipeline(zot)
        note = first['papers']['2311.16922']['review_note_key']
        self.payload['papers'][0]['review_markdown'] = '# Review v2'
        second = self.run_pipeline(zot)
        self.assertEqual(second['runs'][1]['processed'], ['2311.16922'])
        self.assertEqual(second['papers']['2311.16922']['review_note_key'], note)
        self.assertIn('Review v2', zot.items[note]['data']['note'])
        self.assertTrue(second['papers']['2311.16922']['created'])

    def test_failed_pdf_upload_is_retried_on_resume(self):
        self.cfg['attach_pdf'] = 'upload'
        zot = MemoryZotero()
        zot.upload_file = lambda *a: None

        class Pdf:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'%PDF-1.7 ...'

        with patch.object(zp, 'urlopen', side_effect=zp.URLError('offline')):
            first = self.run_pipeline(zot)
        self.assertEqual(first['stage'], 'papers_saved')
        self.assertTrue(first['papers']['2311.16922']['pdf_fallback'])
        with patch.object(zp, 'urlopen', return_value=Pdf()) as fetch:
            second = self.run_pipeline(zot)
        fetch.assert_called_once()
        self.assertEqual(second['runs'][1]['processed'], ['2311.16922'])
        self.assertNotIn('pdf_fallback', second['papers']['2311.16922'])
        pdf = zot.items[second['papers']['2311.16922']['pdf_key']]['data']
        self.assertEqual(pdf['linkMode'], 'imported_file')
        self.assertTrue(second['papers']['2311.16922']['created'])

    def test_record_of_another_library_instance_is_not_resumed(self):
        zot = MemoryZotero()
        self.run_pipeline(zot)
        other = MemoryZotero()
        other.cache_identity = 'memory://library#other-profile'
        result = self.run_pipeline(other)
        self.assertEqual(len(result['runs']), 1)
        self.assertEqual(result['runs'][0]['processed'], ['2311.16922'])
        self.assertEqual(result['library_identity'], 'memory://library#other-profile')

    def test_error_of_a_paper_moved_to_skip_is_retired(self):
        zot = MemoryZotero()
        with patch.object(zp, 'ensure_paper', side_effect=zp.ZoteroError('paper rejected')), \
                self.assertRaises(SystemExit):
            self.run_pipeline(zot)
        self.payload['papers'][0]['tier'] = 'skip'
        result = self.run_pipeline(zot)
        self.assertEqual(result['stage'], 'papers_saved')
        self.assertEqual(result['errors'], [])
        self.assertIn('2311.16922: paper rejected', result['runs'][0]['errors'])

    def test_record_of_another_day_is_not_resumed(self):
        zot = MemoryZotero()
        self.run_pipeline(zot)
        self.payload['date'] = '2026-11-05'
        result = self.run_pipeline(zot)
        self.assertEqual(result['date'], '2026-11-05')
        self.assertEqual(len(result['runs']), 1)
        self.assertFalse(result['papers']['2311.16922']['created'])

    def test_readonly_dedup_merges_ids_and_retains_existing(self):
        zot = MemoryZotero()
        key = zot.create([zp.item_payload(self.enriched[0], ['CUSTOM01'], [])])[0]
        args = Namespace(input=str(self.path/'input.json'), output=str(self.path/'checked.json'))
        Path(args.input).write_text(json.dumps(self.enriched * 2))
        before = len(zot.events)
        zp.cmd_check_papers(zot, self.cfg, args)
        result = json.loads(Path(args.output).read_text())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['zotero']['item_key'], key)
        self.assertEqual(len(zot.events), before)

    def test_single_save_defaults_to_pending_without_agent_selection(self):
        zot = MemoryZotero()
        args = Namespace(arxiv='2311.16922', collections=None, directions=None,
                         tags=None, classification_reason='')
        with patch.object(zp, 'fetch_arxiv_meta', return_value=self.enriched[0]), \
                patch('builtins.print') as output:
            zp.cmd_upsert(zot, self.cfg, args)
        result = json.loads(output.call_args.args[0])
        self.assertTrue(result['created'])
        pending = next(k for k, c in zot.collections.items() if c['name'] == 'Pending')
        self.assertEqual(result['collection_keys'], [pending])
        self.assertNotIn('Daily Papers', [c['name'] for c in zot.collections.values()])

    def test_note_replacement_reuses_legacy_note_without_system_tags(self):
        zot = MemoryZotero()
        parent = zot.create([zp.item_payload(self.enriched[0], [], [])])[0]
        legacy = zot.create([{'itemType': 'note', 'parentItem': parent,
                             'note': '<h1>Old note</h1>',
                             'tags': [{'tag': 'dp/full-note'}, {'tag': 'my-tag'}]}])[0]
        markdown = self.path / 'note.md'
        markdown.write_text('# VCD\n\nSome content')
        args = Namespace(item=parent, arxiv=None, markdown=str(markdown), tags=None,
                         kind='full-note', replace=False)
        with self.assertRaises(SystemExit):
            zp.cmd_add_note(zot, self.cfg, args)
        args.replace = True
        zp.cmd_add_note(zot, self.cfg, args)
        self.assertEqual(len(zot.items), 2)
        self.assertEqual(zot.items[legacy]['data']['tags'], [{'tag': 'my-tag'}])
        self.assertEqual(zp.note_summary(zot.item(legacy))['kinds'], ['full-note'])
        args.replace = False
        with self.assertRaises(SystemExit):
            zp.cmd_add_note(zot, self.cfg, args)

    def test_note_type_and_date_matching(self):
        for kind in ('review', 'digest', 'full-note', 'quick'):
            day = '2026-11-04' if kind in ('review', 'digest') else None
            markdown = zp.identify_note('# A paper', kind, day)
            self.assertEqual(zp.identify_note(markdown, kind, day), markdown)
            data = {'note': zp.to_note_html(markdown), 'tags': []}
            self.assertTrue(zp.note_matches(data, kind, day))
            if day:
                self.assertFalse(zp.note_matches(data, kind, '2026-11-05'))


    def test_english_note_titles_and_cross_language_recognition(self):
        from unittest import mock
        with mock.patch.object(zp, 'language', return_value='en'):
            review = zp.identify_note('# EGDR: A paper', 'review', '2026-11-04')
            self.assertEqual(review.splitlines()[0], '# [Daily Review] 2026-11-04 EGDR: A paper')
            self.assertEqual(zp.identify_note(review, 'review', '2026-11-04'), review)
            self.assertEqual(zp.identify_note('# X', 'full-note', None), '# [Deep Read] X')
            self.assertEqual(zp.identify_note('# X', 'my-kind', None), '# [Note-my-kind] X')
            # A Chinese note is recognized and its title rewritten in English.
            self.assertEqual(zp.identify_note('# 【精读】X', 'full-note', None), '# [Deep Read] X')
        for title, kind, day in (('[Daily Digest] 2026-11-04 🔪', 'digest', '2026-11-04'),
                                 ('【每日推荐】2026-11-04 🔪', 'digest', '2026-11-04'),
                                 ('[Quick Read] X', 'quick', None), ('【笔记-my-kind】X', 'my-kind', None)):
            data = {'note': zp.to_note_html(f'# {title}'), 'tags': []}
            self.assertEqual(zp.note_identity(data), ([kind], day))
        self.assertEqual(zp.note_identity({'note': zp.to_note_html('# [Draft] X'), 'tags': []}), ([], None))


def installed_shared(tmp: str, config: dict | None = None) -> Path:
    """A copy of _zotero_shared as an install has it, optionally with its own config.json."""
    import shutil
    shared = Path(tmp) / '_zotero_shared'
    shutil.copytree(ROOT / 'skills/_zotero_shared', shared,
                    ignore=shutil.ignore_patterns('__pycache__', 'config.json'))
    if config is not None:
        (shared / 'config.json').write_text(json.dumps(config), encoding='utf-8')
    return shared


def run_config(shared: Path, *args, **env):
    full_env = {**os.environ, 'XDG_CONFIG_HOME': str(shared.parent), **env}
    full_env.pop('ZDP_CONFIG', None)
    if 'ZDP_LANGUAGE' not in env:
        full_env.pop('ZDP_LANGUAGE', None)
    return subprocess.run([sys.executable, str(shared / 'zdp_config.py'), *args],
                          env=full_env, capture_output=True, text=True)


class LanguageConfigTests(unittest.TestCase):
    def effective(self, config=None, **env):
        with tempfile.TemporaryDirectory() as tmp:
            run = run_config(installed_shared(tmp, config), **env)
            self.assertEqual(run.returncode, 0, run.stderr)
            return json.loads(run.stdout)

    def test_localized_defaults_and_overrides(self):
        en = self.effective()
        self.assertEqual((en['language'], en['zotero']['pending_collection']), ('en', 'Pending'))
        self.assertEqual(en['daily_papers']['directions'][0]['name'], 'MLLM Hallucination')
        zh = self.effective({'language': 'zh'})
        self.assertEqual(zh['zotero']['pending_collection'], '待定')
        self.assertEqual(zh['daily_papers']['directions'], en['daily_papers']['directions'])
        explicit = self.effective({'language': 'zh', 'zotero': {'pending_collection': 'Inbox'}})
        self.assertEqual(explicit['zotero']['pending_collection'], 'Inbox')
        self.assertEqual(self.effective({'language': 'zh'}, ZDP_LANGUAGE='en')['language'], 'en')

    def test_config_lives_in_the_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = installed_shared(tmp, {'language': 'zh'})
            result = json.loads(run_config(shared).stdout)
            self.assertEqual(result['runtime']['config'], str(shared.resolve() / 'config.json'))
            self.assertEqual(result['language'], 'zh')

    def test_invalid_language_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = run_config(installed_shared(tmp), ZDP_LANGUAGE='fr')
            self.assertNotEqual(run.returncode, 0)
            self.assertIn('language must be one of', run.stderr)

    def test_init_without_lang_uses_effective_language(self):
        for env_lang, expected in (('', 'en'), ('zh', 'zh')):
            with tempfile.TemporaryDirectory() as tmp:
                shared = installed_shared(tmp)
                run = run_config(shared, '--init', ZDP_LANGUAGE=env_lang)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(json.loads((shared / 'config.json').read_text())['language'], expected)

    def test_set_language_preserves_other_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = installed_shared(tmp, {'zotero': {'pending_collection': 'Inbox'}})
            run = run_config(shared, '--set-language', 'en')
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads((shared / 'config.json').read_text()),
                             {'zotero': {'pending_collection': 'Inbox'}, 'language': 'en'})


class ClassificationSettingTests(unittest.TestCase):
    def test_default_and_validation(self):
        self.assertEqual(classification_mode({}), 'pending')
        self.assertEqual(classification_mode({'classification_mode': 'existing'}), 'existing')
        with self.assertRaises(ValueError):
            classification_mode({'classification_mode': 'typo'})

    def test_setting_preserves_other_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = installed_shared(tmp, {'zotero': {'pending_collection': 'Inbox'}, 'daily_papers': {'top_n': 4}})
            run = run_config(shared, '--set-classification', 'existing')
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads((shared / 'config.json').read_text())
            self.assertEqual(result['zotero']['classification_mode'], 'existing')
            self.assertEqual(result['zotero']['pending_collection'], 'Inbox')
            self.assertEqual(result['daily_papers']['top_n'], 4)


if __name__ == '__main__':
    unittest.main()
