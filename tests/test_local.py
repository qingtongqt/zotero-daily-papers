import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/_zotero_shared'))
import zotero_push as zp


class LocalTests(unittest.TestCase):
    def test_file_uri_to_path_handles_windows_drive_letters(self):
        self.assertEqual(zp.file_uri_to_path('file:///C:/Users/me/Papers/A%20B.pdf', windows=True),
                         'C:/Users/me/Papers/A B.pdf')
        self.assertEqual(zp.file_uri_to_path('file://localhost/D:/x.pdf', windows=True), 'D:/x.pdf')
        self.assertEqual(zp.file_uri_to_path('file:///home/me/x.pdf', windows=True), '/home/me/x.pdf')
        self.assertEqual(zp.file_uri_to_path('file:///home/me/x.pdf', windows=False), '/home/me/x.pdf')
        self.assertEqual(zp.file_uri_to_path('file:///C:/x.pdf', windows=False), '/C:/x.pdf')
        for bad in ('https://example.com/x.pdf', 'file://server/share/x.pdf'):
            with self.assertRaises(zp.ZoteroError):
                zp.file_uri_to_path(bad, windows=True)

    def client(self):
        z = zp.LocalZotero({}, dry_run=True)
        z.dry_run = False
        z.server_id = 'instance-A'
        z.key = 'local-key'
        return z

    def test_read_does_not_send_key_and_write_is_instance_bound(self):
        z = self.client()
        with patch.object(z, '_request', return_value=(200, {}, b'[]')) as request:
            z.get('/items')
            self.assertNotIn('Zotero-API-Key', request.call_args.args[3])
            z.patch('ABCDEFGH', 7, {'title': 'New'})
            headers = request.call_args.args[3]
            self.assertEqual(headers['Zotero-API-Key'], 'local-key')
            self.assertEqual(headers['Zotero-Server-ID'], 'instance-A')
            self.assertEqual(headers['If-Unmodified-Since-Version'], '7')

    def test_expired_authorization_retries_once(self):
        z = self.client()
        err = HTTPError(z.base, 401, 'Unauthorized', {}, None)
        with patch.object(z, '_request', side_effect=[err, (204, {}, b'')]) as request, \
                patch.object(z, 'authorize') as authorize:
            z.patch('ABCDEFGH', 7, {})
            self.assertEqual(request.call_count, 2)
            authorize.assert_called_once()

    def test_ambiguous_write_is_not_retried(self):
        z = self.client()
        with patch.object(z, '_request', side_effect=URLError('connection lost')) as request:
            with self.assertRaises(zp.ZoteroError):
                z.patch('ABCDEFGH', 7, {})
            self.assertEqual(request.call_count, 1)

    def test_attanger_file_outside_storage(self):
        z = self.client()
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / '中文 paper.pdf'
            file.write_bytes(b'%PDF-1.7')
            with patch.object(z, '_raw', return_value=(200, {}, file.as_uri().encode())):
                info = z.file_info({'key': 'ABCDEFGH', 'data': {'linkMode': 'linked_file'}})
            self.assertEqual(info['path'], str(file))
            self.assertTrue(info['exists'])

    def test_cache_rejects_other_instance(self):
        z = self.client()
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'cache.json'
            cache.write_text(json.dumps({'identity': 'other', 'items': {'x': 'ABCDEFGH'}}))
            with patch.object(zp, 'cache_path', return_value=cache):
                self.assertEqual(zp.load_cache(z), {})

    def test_dry_run_never_connects_or_downloads(self):
        with patch.object(zp, 'urlopen', side_effect=AssertionError('network')):
            z = zp.LocalZotero({}, dry_run=True)
            key = zp.ensure_pdf(z, 'DRY00001', '2311.16922', 'upload', [])
            self.assertTrue(key.startswith('DRY'))

    def test_remote_origin_rejected(self):
        with self.assertRaises(zp.ZoteroError):
            zp.LocalZotero({'local_url': 'http://example.com:23119'}, dry_run=True)

    def test_existing_linked_pdf_preserved(self):
        z = self.client()
        kids = [{'key': 'LINKED01', 'data': {'itemType': 'attachment',
                  'contentType': 'application/pdf', 'linkMode': 'linked_file'}}]
        with patch.object(z, 'create', side_effect=AssertionError('must preserve attachment')), \
                patch.object(z, 'file_info', return_value={'exists': True, 'path': '/od/x.pdf'}):
            self.assertEqual(zp.ensure_pdf(z, 'PARENT01', '2311.16922', 'upload', kids), 'LINKED01')

    def test_web_backend_still_uses_cloud(self):
        z = zp.Zotero({'api_key': 'cloud-key', 'library_id': '123'})
        self.assertEqual(z.base, 'https://api.zotero.org/users/123')


if __name__ == '__main__':
    unittest.main()
