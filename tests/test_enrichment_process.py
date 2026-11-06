import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/zotero-daily-papers/scripts'))
import enrich_papers as ep


def process(output=b'', code=0):
    proc = Mock()
    proc.returncode = code
    proc.communicate = AsyncMock(return_value=(output, b''))
    return proc


class EnrichmentProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_shell_syntax_is_rejected_before_any_process_or_network(self):
        with patch.object(ep.asyncio, 'create_subprocess_exec', new_callable=AsyncMock) as execute, \
                patch.object(ep, 'curl_fetch', new_callable=AsyncMock) as fetch:
            for aid in ('2311.16922$(printf TEST)', '2311.16922;echo TEST', '2311.16922\n', '../2311.16922'):
                with self.assertRaises(ValueError):
                    await ep.extract_affiliations_pdf(aid, asyncio.Semaphore(1))
                paper = {'arxiv_id': aid}
                self.assertEqual(await ep.enrich_one(paper, asyncio.Semaphore(1)), paper)
            execute.assert_not_called()
            fetch.assert_not_called()

    async def test_bytes_pass_between_explicit_commands(self):
        pdf = process(b'%PDF-1.7 test')
        text = process(b'Example University')
        parser = process(b'{"affiliations":["Example University"]}')
        with patch.object(ep.asyncio, 'create_subprocess_exec', new_callable=AsyncMock,
                          side_effect=[pdf, text, parser]) as execute, \
                patch.object(ep.asyncio, 'create_subprocess_shell', new_callable=AsyncMock) as shell:
            result = await ep.extract_affiliations_pdf('2311.16922v2', asyncio.Semaphore(1), retries=1)
        self.assertEqual(result, ['Example University'])
        self.assertEqual(execute.call_args_list[0].args,
                         ('curl', '-fsSL', '--max-time', str(ep.CURL_TIMEOUT), 'https://arxiv.org/pdf/2311.16922v2'))
        self.assertEqual(execute.call_args_list[1].args, ('pdftotext', '-l', '2', '-', '-'))
        text.communicate.assert_awaited_once_with(b'%PDF-1.7 test')
        parser.communicate.assert_awaited_once_with(b'Example University')
        shell.assert_not_called()

    async def test_nonzero_download_does_not_run_parser(self):
        with patch.object(ep.asyncio, 'create_subprocess_exec', new_callable=AsyncMock,
                          return_value=process(b'error page', 22)) as execute:
            result = await ep.extract_affiliations_pdf('2311.16922', asyncio.Semaphore(1), retries=1)
        self.assertEqual(result, [])
        execute.assert_awaited_once()

    async def test_timeout_kills_and_reaps_child(self):
        proc = process(code=None)
        proc.communicate.side_effect = [asyncio.TimeoutError(), (b'', b'')]
        with patch.object(ep.asyncio, 'create_subprocess_exec', new_callable=AsyncMock, return_value=proc):
            with self.assertRaises(asyncio.TimeoutError):
                await ep.run_process('curl', 'unused', timeout=0.01)
        proc.kill.assert_called_once()
        self.assertEqual(proc.communicate.await_count, 2)

    async def test_cancellation_kills_and_reaps_child(self):
        proc = process(code=None)
        proc.communicate.side_effect = [asyncio.CancelledError(), (b'', b'')]
        with patch.object(ep.asyncio, 'create_subprocess_exec', new_callable=AsyncMock, return_value=proc):
            with self.assertRaises(asyncio.CancelledError):
                await ep.run_process('pdftotext', '-')
        proc.kill.assert_called_once()
        self.assertEqual(proc.communicate.await_count, 2)

    def test_supported_ids(self):
        for aid in ('2311.16922', '2311.16922v2', '1706.03762', 'hep-th/9901001v3'):
            self.assertTrue(ep.valid_arxiv_id(aid), aid)


if __name__ == '__main__':
    unittest.main()
