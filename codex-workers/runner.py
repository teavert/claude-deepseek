"""Shared Windows Codex admission queue. No prompts or credentials enter SQLite.

Each lightweight supervisor owns a kill-on-close Job Object BEFORE launching a
worker. Its queue reservation lives until that worker exits, including failures.
All callers use the same database beside this file; the limit is fixed at two.
"""
import argparse
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

LIMIT = 2
DB = Path(__file__).with_name('queue.sqlite3')
k = ctypes.WinDLL('kernel32', use_last_error=True)
for name, args, result in [
    ('OpenProcess', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
    ('CloseHandle', [w.HANDLE], w.BOOL),
    ('GetProcessTimes', [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
    ('WaitForSingleObject', [w.HANDLE, w.DWORD], w.DWORD),
    ('CreateJobObjectW', [ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
    ('SetInformationJobObject', [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
    ('AssignProcessToJobObject', [w.HANDLE, w.HANDLE], w.BOOL),
    ('GetCurrentProcess', [], w.HANDLE),
]:
    fn = getattr(k, name)
    fn.argtypes, fn.restype = args, result


def identity(pid):
    handle = k.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # process no longer exists
            return None
        raise ctypes.WinError(ctypes.get_last_error())  # fail closed
    try:
        if k.WaitForSingleObject(handle, 0) == 0:
            return None
        times = [w.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        k.CloseHandle(handle)


def protect_tree():
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                    ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                    ('active', w.DWORD), ('affinity', ctypes.c_size_t),
                    ('priority', w.DWORD), ('scheduling', w.DWORD)]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', ctypes.c_uint64 * 6),
                    ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    handle = k.CreateJobObjectW(None, None)
    info = Extended()
    info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not k.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not k.AssignProcessToJobObject(handle, k.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    # Keep the non-inheritable handle open until supervisor exit. Unexpected exit
    # kills the worker too; never recycle a slot while its worker is still alive.
    return handle


class Queue:
    def __init__(self, database=DB):
        self.db = sqlite3.connect(database, timeout=30, isolation_level=None)
        self.db.execute('CREATE TABLE IF NOT EXISTS workers (id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER, born TEXT, state TEXT, child INTEGER, child_born TEXT)')
        self.ticket = self.db.execute('INSERT INTO workers(pid,born,state) VALUES(?,?,?)',
                                      (os.getpid(), identity(os.getpid()), 'queued')).lastrowid

    def acquire(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for ticket, pid, born, child, child_born in self.db.execute('SELECT id,pid,born,child,child_born FROM workers').fetchall():
                if identity(pid) != born and (not child or identity(child) != child_born):
                    self.db.execute('DELETE FROM workers WHERE id=?', (ticket,))
            running = self.db.execute("SELECT count(*) FROM workers WHERE state='running'").fetchone()[0]
            head = self.db.execute("SELECT id FROM workers WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
            admitted = running < LIMIT and head and head[0] == self.ticket
            if admitted:
                self.db.execute("UPDATE workers SET state='running' WHERE id=?", (self.ticket,))
            self.db.execute('COMMIT')
            return bool(admitted)
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def child(self, pid):
        born = identity(pid)
        self.db.execute('UPDATE workers SET child=?,child_born=? WHERE id=?', (pid if born else None, born, self.ticket))

    def release(self):
        self.db.execute('DELETE FROM workers WHERE id=?', (self.ticket,))
        self.db.close()


def publish_status(status, state):
    """Readers can briefly deny rename sharing on Windows; retry boundedly."""
    temporary = status.with_suffix('.tmp')
    temporary.write_text(json.dumps({'state': state}), encoding='utf-8')
    for attempt in range(100):
        try:
            os.replace(temporary, status)
            return
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in {5, 32, 33} or attempt == 99:
                raise
            time.sleep(.01)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--status', required=True)
    parser.add_argument('--timeout', type=float, required=True)
    parser.add_argument('--require-chatgpt', action='store_true')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    status = Path(args.status)
    def report(state):
        publish_status(status, state)
    job_handle = protect_tree()
    parent, parent_born = os.getppid(), identity(os.getppid())
    queue, child = Queue(), None
    try:
        report('queued')
        while not queue.acquire():
            if identity(parent) != parent_born:
                return 130
            time.sleep(0.1)
        if identity(parent) != parent_born:
            return 130
        if args.require_chatgpt:
            # Authenticate inside the same reservation, never launch unbounded
            # login checks ahead of the queue. Do not relay authentication output.
            check = subprocess.run([command[0], 'login', 'status'], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            if check.returncode or b'Logged in using ChatGPT' not in check.stdout:
                report('failed_auth')
                return 126
        # Inherit pipes without buffering the prompt or any output in this helper.
        child = subprocess.Popen(command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        queue.child(child.pid)
        report('running')
        deadline = time.monotonic() + args.timeout
        while child.poll() is None:
            if identity(parent) != parent_born or time.monotonic() >= deadline:
                report('timed_out' if time.monotonic() >= deadline else 'cancelled')
                # Exit closes the Job Object, killing the entire owned tree. Leave
                # the reservation for stale cleanup after both processes exit.
                os._exit(124 if time.monotonic() >= deadline else 130)
            time.sleep(0.1)
        report('exited')
        return child.returncode
    finally:
        if child is None or child.poll() is not None:
            queue.release()
        # The OS closes job_handle on process exit (including abrupt termination).


if __name__ == '__main__':
    sys.exit(main())
