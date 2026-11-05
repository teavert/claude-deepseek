import asyncio
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from gpt_main import GptMain, GptFailure


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_queues_requests_and_cancels_waiters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / '.claude-gateway' / 'codex-workers' / 'runner.py'
            runner.parent.mkdir(parents=True)
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'codex-workers/runner.py', runner)
            active = peak = 0
            admitted = asyncio.Event()
            release = asyncio.Event()

            class FakeGpt(GptMain):
                def command(self):
                    return [sys.executable, '-u', '-c', 'import time; print("ready"); time.sleep(30)']

                async def exchange(self, process, *args, **kwargs):
                    nonlocal active, peak
                    self.assertion = await process.stdout.readline()
                    active += 1
                    peak = max(peak, active)
                    if active == 2:
                        admitted.set()
                    try:
                        await release.wait()
                        return 'ok'
                    finally:
                        active -= 1

            with patch('gpt_main.Path.home', return_value=root):
                adapter = FakeGpt({'response_timeout_seconds': 10, 'worker_runner': str(runner)})
                tasks = [asyncio.create_task(adapter.complete({'messages': []})) for _ in range(5)]
                try:
                    await asyncio.wait_for(admitted.wait(), 8)
                    await asyncio.sleep(.3)
                    self.assertEqual(active, 2)
                    tasks[-1].cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await tasks[-1]
                    release.set()
                    self.assertEqual(await asyncio.wait_for(asyncio.gather(*tasks[:-1]), 8), ['ok'] * 4)
                    self.assertEqual(peak, 2)
                    self.assertEqual(adapter.assertion.strip(), b'ready')
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
