import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from argparse import Namespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/_zotero_shared'))
import zotero_push as zp


class ClassificationTests(unittest.TestCase):
    def test_catalog_preserves_duplicate_names_and_parent_paths(self):
        zot = Mock()
        zot.get_all.return_value = [
            {"key": "ROOT", "data": {"name": "Research", "parentCollection": False}},
            {"key": "CHILD", "data": {"name": "Alignment", "parentCollection": "ROOT"}},
            {"key": "OTHER", "data": {"name": "Alignment", "parentCollection": False}}]
        zot.get.return_value = [{"data": {"title": "Example paper"}}]
        rows = zp.collection_catalog(zot, samples=True)
        self.assertEqual([r['path'] for r in rows], ['Research', 'Research / Alignment', 'Alignment'])
        self.assertEqual(rows[1]['example_titles'], ['Example paper'])
        zot.create.assert_not_called()

    def test_existing_collection_selected_without_creating_predefined_tree(self):
        zot = Mock()
        catalog = [{'key': 'CUSTOM', 'name': 'My Research', 'parent': None}]
        self.assertEqual(zp.select_collections(zot, {}, ['CUSTOM'], catalog), ['CUSTOM'])
        zot.create.assert_not_called()

    def test_no_match_creates_and_reuses_pending(self):
        zot = Mock()
        zot.create.return_value = ['PENDING']
        catalog = []
        self.assertEqual(zp.select_collections(zot, {}, [], catalog), ['PENDING'])
        self.assertEqual(zp.select_collections(zot, {}, [], catalog), ['PENDING'])
        zot.create.assert_called_once_with([{'name': 'Pending'}], path='/collections')

    def test_unknown_or_deleted_key_fails_without_write(self):
        zot = Mock()
        with self.assertRaises(zp.ZoteroError):
            zp.select_collections(zot, {}, ['DELETED'], [])
        zot.create.assert_not_called()

    def test_unspecified_selection_requests_agent_judgment_without_write(self):
        zot = Mock()
        zot.cache_identity = 'local-test'
        zot.get_all.return_value = []
        args = Namespace(arxiv='2311.16922', collections=None)
        with patch.object(zp, 'fetch_arxiv_meta', return_value={'title': 'A paper'}), \
             patch.object(zp, 'load_cache', return_value={}), patch('builtins.print') as output:
            zp.cmd_upsert(zot, {"classification_mode": "existing"}, args)
        self.assertIn('awaiting_classification', output.call_args.args[0])
        zot.create.assert_not_called()
        zot.patch.assert_not_called()

    def test_existing_user_collections_preserved(self):
        zot = Mock()
        found = {'key': 'PAPER001', 'version': 8, 'data': {
            'collections': ['PERSONAL'], 'tags': [{'tag': 'mine'}]}}
        with patch.object(zp, 'find_item', return_value=found):
            zp.ensure_paper(zot, {'arxiv_id': '2311.16922'}, ['CUSTOM'], ['dp'], {})
        self.assertEqual(zot.patch.call_args.args[2]['collections'], ['PERSONAL', 'CUSTOM'])
        zot.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
