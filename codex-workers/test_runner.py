"""Real cross-process admission/cleanup tests; no Codex or paid model calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

RUNNER = Path(__file__).with_name('runner.py')


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # Isolated database, identical supervisor code.
        self.runner = self.root / 'runner.py'
        shutil.copyfile(RUNNER, self.runner)
        self.processes = []

    def tearDown(self):
        for p in self.processes:
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=10)
        self.temp.cleanup()

    def start(self, name, code='import time; time.sleep(1)', timeout=30, node=False):
        status = self.root / (name + '.json')
        command = [sys.executable, str(self.runner), '--status', str(status), '--timeout', str(timeout),
                   '--', sys.executable, '-u', '-c', code]
        if node:
            # Same Node spawn + pipe layout as the reviewer bridge.
            js = "const {spawn}=require('node:child_process'); const c=spawn(process.argv[1], JSON.parse(process.argv[2]), {stdio:['pipe','pipe','pipe'],windowsHide:true}); c.stdout.pipe(process.stdout); c.stderr.pipe(process.stderr); process.stdin.pipe(c.stdin); c.on('close',n=>process.exit(n));"
            command = ['node', '-e', js, command[0], json.dumps(command[1:])]
        p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(p)
        return p, status

    def wait_state(self, status, wanted):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if json.loads(status.read_text())['state'] == wanted:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(.03)
        details = status.read_text() if status.exists() else 'missing status'
        for process in self.processes:
            if process.poll() is not None:
                _, error = process.communicate()
                details += repr(error)
        self.fail(f'{status.name} never reached {wanted}: {details}')

    def test_shared_two_worker_fifo_and_pipes(self):
        code = "import sys,time; print('ready',flush=True); sys.stdin.readline(); print('done',flush=True)"
        a, sa = self.start('a', code)
        b, sb = self.start('b', code, node=True)
        self.wait_state(sa, 'running')
        self.wait_state(sb, 'running')
        c, sc = self.start('c', code)
        self.wait_state(sc, 'queued')
        d, sd = self.start('d', code, node=True)
        self.wait_state(sd, 'queued')
        time.sleep(.3)
        self.assertEqual(json.loads(sc.read_text())['state'], 'queued')
        self.assertEqual(json.loads(sd.read_text())['state'], 'queued')
        self.assertEqual(a.communicate(b'finish\n', timeout=5)[0], b'ready\r\ndone\r\n')
        self.wait_state(sc, 'running')
        self.assertEqual(json.loads(sd.read_text())['state'], 'queued')
        b.communicate(b'finish\n', timeout=5)
        self.wait_state(sd, 'running')
        c.communicate(b'finish\n', timeout=5)
        d.communicate(b'finish\n', timeout=5)

    def test_queue_wait_does_not_spend_execution_timeout(self):
        a, sa = self.start('a')
        b, sb = self.start('b')
        self.wait_state(sa, 'running')
        self.wait_state(sb, 'running')
        c, sc = self.start('c', 'print("ok")', timeout=.3)
        self.wait_state(sc, 'queued')
        out, err = c.communicate(timeout=5)
        self.assertEqual(c.returncode, 0, err)
        self.assertIn(b'ok', out)

    def test_timeout_and_spawn_failure_release_slots(self):
        a, sa = self.start('a', timeout=.2)
        a.communicate(timeout=5)
        self.assertEqual(a.returncode, 124)
        self.assertEqual(json.loads(sa.read_text())['state'], 'timed_out')
        # Syntax failure is an ordinary worker failure, and must release a slot.
        b, sb = self.start('b', 'invalid python syntax')
        b.communicate(timeout=5)
        self.assertNotEqual(b.returncode, 0)
        c, sc = self.start('c', 'print("recovered")')
        self.assertIn(b'recovered', c.communicate(timeout=5)[0])

    def test_cancel_queued_and_kill_running_tree(self):
        pidfile = self.root / 'pid'
        code = f'import os,time; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(30)'
        a, sa = self.start('a', code)
        b, sb = self.start('b', 'import time; time.sleep(30)')
        self.wait_state(sa, 'running')
        self.wait_state(sb, 'running')
        c, sc = self.start('c', 'print("must not launch")')
        self.wait_state(sc, 'queued')
        c.kill()
        self.assertEqual(c.communicate(timeout=5)[0], b'')
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        pid = int(pidfile.read_text())
        a.kill()
        a.communicate(timeout=5)
        from runner import identity
        deadline = time.monotonic() + 5
        while identity(pid) is not None and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertIsNone(identity(pid))
        d, sd = self.start('d', 'print("next")')
        self.assertIn(b'next', d.communicate(timeout=5)[0])


if __name__ == '__main__':
    unittest.main()
