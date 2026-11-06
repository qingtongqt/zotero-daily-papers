import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/_zotero_shared'))
import zotero_push as zp  # noqa: E402


def local_client():
    z = zp.LocalZotero({}, dry_run=True)
    z.dry_run, z.server_id, z.key = False, 'instance-A', 'local-key'
    return z


class PendingCollectionTests(unittest.TestCase):
    def test_filed_existing_item_is_not_added_to_pending(self):
        found = {'key': 'ITEM', 'version': 3, 'data': {'collections': ['CLASSIC'], 'tags': []}}
        z = local_client()
        with patch.object(zp, 'find_item', return_value=found), patch.object(z, 'patch') as patch_:
            zp.ensure_paper(z, {'arxiv_id': '1706.03762'}, ['PENDING'], [], {}, fallback_collection='PENDING')
        patch_.assert_not_called()

    def test_unfiled_existing_item_goes_to_pending(self):
        found = {'key': 'ITEM', 'version': 3, 'data': {'collections': [], 'tags': []}}
        z = local_client()
        with patch.object(zp, 'find_item', return_value=found), patch.object(z, 'patch') as patch_:
            zp.ensure_paper(z, {'arxiv_id': '1706.03762'}, ['PENDING'], [], {}, fallback_collection='PENDING')
        self.assertEqual(patch_.call_args.args[2]['collections'], ['PENDING'])

    def test_explicit_choice_is_still_added(self):
        found = {'key': 'ITEM', 'version': 3, 'data': {'collections': ['CLASSIC'], 'tags': []}}
        z = local_client()
        with patch.object(zp, 'find_item', return_value=found), patch.object(z, 'patch') as patch_:
            zp.ensure_paper(z, {'arxiv_id': '1706.03762'}, ['HALLU'], [], {})
        self.assertEqual(patch_.call_args.args[2]['collections'], ['CLASSIC', 'HALLU'])


class PdfFallbackTests(unittest.TestCase):
    LINK = {'key': 'LINK0001', 'data': {'itemType': 'attachment', 'linkMode': 'linked_url',
                                        'contentType': 'application/pdf',
                                        'url': 'https://arxiv.org/pdf/2311.16922'}}

    def test_upload_mode_retries_download_when_only_a_link_exists(self):
        z = local_client()
        with patch.object(zp, 'urlopen', side_effect=URLError('offline')) as fetch, \
                patch.object(z, 'create') as create:
            key = zp.ensure_pdf(z, 'PARENT01', '2311.16922', 'upload', [self.LINK])
        fetch.assert_called_once()        # it tried again
        create.assert_not_called()        # and did not stack a second link
        self.assertEqual(key, 'LINK0001')

    def test_link_mode_keeps_existing_link(self):
        z = local_client()
        with patch.object(zp, 'urlopen') as fetch:
            self.assertEqual(zp.ensure_pdf(z, 'PARENT01', '2311.16922', 'link', [self.LINK]), 'LINK0001')
        fetch.assert_not_called()


class TranslatorParityTests(unittest.TestCase):
    def test_item_matches_manual_add_by_identifier(self):
        meta = {'arxiv_id': '1706.03762', 'title': 'Attention Is All You Need', 'authors': 'Ashish Vaswani',
                'date': '2023-08-02', 'category': 'cs.CL', 'categories': ['cs.CL', 'cs.LG']}
        obj = zp.item_payload(meta, ['C'], ['大模型架构'])
        self.assertEqual(obj['extra'], 'arXiv:1706.03762 [cs.CL]')
        self.assertEqual(obj['date'], '2023-08-02')
        self.assertEqual(obj['libraryCatalog'], 'arXiv.org')
        self.assertEqual(obj['archiveID'], 'arXiv:1706.03762')
        self.assertEqual(obj['tags'][:2], [
            {'tag': 'Computer Science - Computation and Language', 'type': 1},
            {'tag': 'Computer Science - Machine Learning', 'type': 1}])
        self.assertEqual(obj['tags'][2], {'tag': '大模型架构'})

    def test_arxiv_entry_uses_latest_version_date(self):
        import xml.etree.ElementTree as ET
        xml = ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>'
               '<id>http://arxiv.org/abs/1706.03762v7</id><updated>2023-08-02T00:41:18Z</updated>'
               '<published>2017-06-12T17:57:34Z</published><title>Attention Is\n All You Need</title>'
               '<summary>s</summary><author><name>Ashish Vaswani</name></author>'
               '<arxiv:primary_category term="cs.CL"/><category term="cs.CL"/><category term="cs.LG"/>'
               '</entry></feed>')
        ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
        meta = zp._parse_arxiv_entry(ET.fromstring(xml).find('a:entry', ns), ns)
        self.assertEqual((meta['arxiv_id'], meta['date'], meta['published']), ('1706.03762', '2023-08-02', '2017-06-12'))
        self.assertEqual(meta['title'], 'Attention Is All You Need')
        self.assertEqual(meta['categories'], ['cs.CL', 'cs.LG'])


class DedupPagingTests(unittest.TestCase):
    def test_match_beyond_first_page_is_found(self):
        z = local_client()
        citing = [{'key': f'C{i}', 'data': {'itemType': 'preprint', 'url': 'x'}} for i in range(100)]
        real = {'key': 'REAL', 'data': {'itemType': 'preprint', 'url': 'http://arxiv.org/abs/2311.16922'}}
        with patch.object(z, 'get', side_effect=[citing, [real]]):
            self.assertEqual(zp.find_item(z, '2311.16922', {})['key'], 'REAL')


class AttangerTests(unittest.TestCase):
    CFG = {'attach_pdf': 'upload', 'attanger_wait_seconds': 0.01}

    def kids(self, mode):
        return [{'key': 'ATT', 'data': {'itemType': 'attachment', 'linkMode': mode, 'contentType': 'application/pdf'}}]

    def test_moved_to_linked_file(self):
        z = local_client()
        with patch.object(zp, 'children', return_value=self.kids('linked_file')), \
                patch.object(z, 'file_info', return_value={'key': 'NEWLINK1', 'exists': True,
                                                         'link_mode': 'linked_file', 'path': '/od/x.pdf'}):
            self.assertEqual(zp.wait_for_attanger(z, 'ITEM', self.CFG),
                             {'status': 'moved', 'path': '/od/x.pdf', 'key': 'NEWLINK1'})

    def test_upsert_reports_replacement_attachment_key(self):
        import io, contextlib
        from argparse import Namespace
        z = local_client()
        args = Namespace(arxiv='2311.16922', collections=[], directions=[], tags=[], classification_reason='')
        out = io.StringIO()
        with patch.object(zp, 'fetch_arxiv_meta', return_value={'arxiv_id': '2311.16922', 'title': 'VCD'}), \
                patch.object(zp, 'collection_catalog', return_value=[]), \
                patch.object(zp, 'select_collections', return_value=['PENDING']), \
                patch.object(zp, 'ensure_paper', return_value=('ITEM', True, [])), \
                patch.object(zp, 'children', return_value=[]), \
                patch.object(zp, 'ensure_pdf', return_value='STORED01'), \
                patch.object(zp, 'wait_for_attanger', return_value={'status': 'moved', 'path': '/od/x.pdf', 'key': 'NEWLINK1'}), \
                patch.object(zp, 'save_cache'), contextlib.redirect_stdout(out):
            zp.cmd_upsert(z, {'classification_mode': 'pending'}, args)
        self.assertEqual(json.loads(out.getvalue())['pdf_key'], 'NEWLINK1')

    def test_still_in_zotero_storage_is_reported(self):
        z = local_client()
        path = '/Users/u/Zotero/storage/ATT/2311.16922.pdf'
        with patch.object(zp, 'children', return_value=self.kids('imported_file')), \
                patch.object(z, 'file_info', return_value={'exists': True, 'link_mode': 'imported_file', 'path': path}):
            self.assertEqual(zp.wait_for_attanger(z, 'ITEM', self.CFG)['status'], 'not_moved')

    def test_skipped_for_link_mode_and_dry_run(self):
        self.assertEqual(zp.wait_for_attanger(local_client(), 'ITEM', {'attach_pdf': 'link'})['status'], 'skipped')


class VersionHeaderTests(unittest.TestCase):
    def test_reads_x_zotero_version(self):
        headers = {'Zotero-Server-ID': 'S1', 'X-Zotero-Version': '10.0.7'}
        with patch.object(zp.LocalZotero, '_request', return_value=(200, headers, b'')):
            z = zp.LocalZotero({}, dry_run=False)
        self.assertEqual(z.version, '10.0.7')


class CodexReviewRegressionTests(unittest.TestCase):
    def test_extra_mention_does_not_match_other_paper(self):
        other = {'itemType': 'preprint', 'url': 'http://arxiv.org/abs/2401.00001',
                 'archiveID': 'arXiv:2401.00001', 'extra': 'arXiv:2401.00001\nCompares with VCD 2311.16922'}
        self.assertFalse(zp.item_matches(other, '2311.16922'))
        self.assertTrue(zp.item_matches(other, '2401.00001'))

    def test_journal_version_matches_via_extra_line(self):
        pub = {'itemType': 'journalArticle', 'url': 'https://openaccess.thecvf.com/x.html',
               'DOI': '10.1109/CVPR.2024.1', 'extra': 'arXiv: 2311.16922'}
        self.assertTrue(zp.item_matches(pub, '2311.16922'))
        self.assertFalse(zp.item_matches({**pub, 'extra': 'see 2311.16922'}, '2311.16922'))

    def test_broken_linked_file_triggers_fresh_download(self):
        z = local_client()
        kids = [{'key': 'LINKED01', 'data': {'itemType': 'attachment', 'contentType': 'application/pdf',
                                             'linkMode': 'linked_file'}}]

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'%PDF-1.7 x'

        with patch.object(z, 'file_info', return_value={'exists': False, 'path': '/gone.pdf'}), \
                patch.object(zp, 'urlopen', return_value=Resp()), \
                patch.object(z, 'create', return_value=['NEWPDF01']) as create, \
                patch.object(z, 'upload_file') as upload:
            key = zp.ensure_pdf(z, 'PARENT01', '2311.16922', 'upload', kids)
        self.assertEqual(key, 'NEWPDF01')
        create.assert_called_once()
        upload.assert_called_once()

    def test_reported_collections_are_the_final_ones(self):
        found = {'key': 'ITEM', 'version': 3, 'data': {'collections': ['CLASSIC'], 'tags': []}}
        z = local_client()
        with patch.object(zp, 'find_item', return_value=found), patch.object(z, 'patch'):
            _, created, cols = zp.ensure_paper(z, {'arxiv_id': '1706.03762'}, ['PENDING'], [], {},
                                               fallback_collection='PENDING')
        self.assertFalse(created)
        self.assertEqual(cols, ['CLASSIC'])


if __name__ == '__main__':
    unittest.main()
