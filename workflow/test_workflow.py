import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import workflow as w


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = w.project_path(self.root)
        self.db = self.root / 'state' / 'test.db'
        self.session = w.digest('session-one')
        self.proof = self.root / 'proof.txt'
        self.proof.write_text('local fixture test passed\n', encoding='utf-8')
        self.review_file = self.root / 'review.txt'
        self.review_file.write_text('test fixture review; no model was called', encoding='utf-8')
        (self.root / 'subject.py').write_text('print("fixture")', encoding='utf-8')
        self.review = dict(status='completed', reviewer='test reviewer', model='fixture', auth='fixture',
                           reasoning_effort='fixture', summary='Fixture review only', evidence=['review.txt'])
        self.plan = dict(objective='Test workflow persistence', scope='Local disposable fixtures only', tasks=['Implement and verify'])

    def tearDown(self):
        self.tmp.cleanup()

    def start(self):
        with w.database(self.db) as conn:
            return w.start(conn, self.project, self.session, self.plan)

    def call(self, event='PostToolUseFailure', **extra):
        payload = dict(hook_event_name=event, cwd=self.project, session_id='session-one',
                       tool_name='Bash', tool_input={'command': 'fixture-failure'}, tool_use_id='example')
        payload.update(extra)
        with w.database(self.db) as conn:
            return w.hook(conn, payload)

    def finding_spec(self, **extra):
        spec = dict(title='Fixture defect', asset='src/app.py:handler', root_cause='Missing local validation',
                    severity='medium', status='validated', reproduction='Run local fixture test', expected='Input rejected',
                    observed='Input accepted', impact='Fixture incorrect result', remediation='Validate input',
                    evidence=['proof.txt'], subjects=['subject.py'], validation=self.review, verdict='confirmed')
        spec.update(extra)
        return spec

    def test_resume_after_restart_and_compaction(self):
        run = self.start()
        with w.database(self.db) as conn:
            w.checkpoint(conn, self.project, self.session, dict(phase='analysis', summary='Found one local hypothesis', next_action='Verify fixture'))
        for source in ('resume', 'compact', 'startup'):
            result = self.call('SessionStart', source=source)
            self.assertIn(run['id'], result['hookSpecificOutput']['additionalContext'])
            self.assertIn(self.session, result['hookSpecificOutput']['additionalContext'])

    def test_project_and_session_isolation(self):
        self.start()
        with w.database(self.db) as conn:
            self.assertIsNone(w.current(conn, self.project, 'other', False))
            self.assertIsNone(w.current(conn, self.project + '-other', self.session, False))

    def test_start_does_not_overwrite_unfinished_task(self):
        run = self.start()
        with self.assertRaises(ValueError), w.database(self.db) as conn:
            w.start(conn, self.project, self.session, self.plan)
        with w.database(self.db) as conn:
            second = w.start(conn, self.project, self.session, self.plan, new=True)
            self.assertNotEqual(run['id'], second['id'])
            self.assertEqual(2, conn.execute('SELECT count(*) FROM runs').fetchone()[0])

    def test_resume_into_new_session_is_explicit_and_project_scoped(self):
        run = self.start()
        with w.database(self.db) as conn:
            self.assertIsNone(w.current(conn, self.project, 'new-session', False))
            w.resume(conn, self.project, 'new-session', run['id'])
            self.assertEqual(run['id'], w.current(conn, self.project, 'new-session')['id'])
        with self.assertRaises(ValueError), w.database(self.db) as conn:
            w.resume(conn, self.project + '-other', 'other', run['id'])
        with w.database(self.db) as conn:
            w.start(conn, self.project, 'new-session', self.plan, new=True)
        with self.assertRaises(ValueError), w.database(self.db) as conn:
            w.resume(conn, self.project, 'new-session', run['id'])
        with w.database(self.db) as conn:
            w.resume(conn, self.project, 'new-session', run['id'], replace=True)
            self.assertEqual(run['id'], w.current(conn, self.project, 'new-session')['id'])

    def test_identical_failures_warn_on_third_only(self):
        self.assertEqual({}, self.call())
        self.assertEqual({}, self.call())
        self.assertIn('failed repeatedly', self.call()['hookSpecificOutput']['additionalContext'])
        self.assertEqual({}, self.call())

    def test_changed_arguments_and_success_reset_failure_streak(self):
        self.call()
        self.call()
        self.assertEqual({}, self.call(tool_input={'command': 'different fixture'}))
        self.call()
        self.call('PostToolUse')
        self.assertEqual({}, self.call())

    def test_agent_counters_do_not_collide(self):
        for agent in ('agent-a', 'agent-b'):
            self.assertEqual({}, self.call(agent_id=agent))
            self.assertEqual({}, self.call(agent_id=agent))
        self.assertIn('hookSpecificOutput', self.call(agent_id='agent-a'))

    def test_polling_does_not_trigger_loop_reminders(self):
        self.start()
        for _ in range(25):
            self.assertEqual({}, self.call('PostToolUse', tool_name='mcp__codex-subscription__result'))

    def test_interrupt_does_not_count_as_failure(self):
        for _ in range(4):
            self.assertEqual({}, self.call(is_interrupt=True))

    def test_metadata_does_not_store_commands_or_outputs(self):
        secret = 'DO_NOT_STORE_THIS_FAKE_SECRET'
        self.call(tool_input={'command': secret}, error=secret, transcript_path=secret, tool_response=secret)
        self.assertNotIn(secret.encode(), self.db.read_bytes())

    def test_concurrent_hooks_do_not_lose_updates(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: self.call('PostToolUse', tool_input={'n': i}), range(32)))
        with w.database(self.db) as conn:
            row = conn.execute('SELECT data FROM monitors').fetchone()
            self.assertEqual(32, json.loads(row[0])['total'])

    def test_checkpoint_resets_reminder_counter(self):
        self.start()
        for i in range(19):
            self.assertEqual({}, self.call('PostToolUse', tool_input={'n': i}))
        self.assertIn('Progress checkpoint', self.call('PostToolUse', tool_input={'n': 20})['hookSpecificOutput']['additionalContext'])
        with w.database(self.db) as conn:
            w.checkpoint(conn, self.project, self.session, dict(summary='Progress saved', next_action='Continue'))
            self.assertEqual(0, conn.execute('SELECT count(*) FROM monitors').fetchone()[0])

    def test_cannot_complete_without_task_evidence(self):
        self.start()
        with self.assertRaises(ValueError), w.database(self.db) as conn:
            w.checkpoint(conn, self.project, self.session, dict(summary='Done', next_action='', phase='complete',
                tasks=[dict(id='1', status='completed', verification='Passed')]))
        with w.database(self.db) as conn:
            self.assertEqual('pending', w.current(conn, self.project, self.session)['tasks'][0]['status'])

    def test_completion_checks_reviews_and_evidence(self):
        self.start()
        spec = dict(summary='Completed local fixture', next_action='', phase='complete',
                    tasks=[dict(id='1', status='completed', verification='Fixture passed', evidence=['proof.txt'])])
        with self.assertRaises(ValueError), w.database(self.db) as conn:
            w.checkpoint(conn, self.project, self.session, spec)
        spec['reviews'] = dict(planning=self.review, validation=self.review)
        with w.database(self.db) as conn:
            self.assertEqual('complete', w.checkpoint(conn, self.project, self.session, spec)['phase'])
        with self.assertRaises(ValueError), w.database(self.db) as conn:
            w.finding(conn, self.project, self.session, self.finding_spec())

    def test_unavailable_review_is_explicit_limitation(self):
        self.start()
        missing = dict(status='unavailable', summary='No subscription access in fixture')
        with w.database(self.db) as conn:
            run = w.checkpoint(conn, self.project, self.session, dict(summary='Fixture tested', phase='complete',
                tasks=[dict(id='1', status='completed', verification='Passed', evidence=['proof.txt'])],
                reviews=dict(planning=missing, validation=missing)))
            self.assertTrue(any('not recorded as completed' in s for s in w.report_data(run)['limitations']))

    def test_changed_task_evidence_blocks_completion(self):
        self.start()
        with w.database(self.db) as conn:
            w.checkpoint(conn, self.project, self.session, dict(summary='Task done', next_action='Review',
                tasks=[dict(id='1', status='completed', verification='Passed', evidence=['proof.txt'])]))
        self.proof.write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError), w.database(self.db) as conn:
            w.checkpoint(conn, self.project, self.session, dict(summary='Done', phase='complete', reviews=dict(planning=self.review, validation=self.review)))

    def test_validated_finding_requires_reproduction_and_review(self):
        self.start()
        for extra in ({'reproduction': ''}, {'evidence': []}, {'subjects': []}, {'verdict': 'disputed'}, {'validation': dict(status='unavailable', summary='No review')}):
            with self.assertRaises(ValueError), w.database(self.db) as conn:
                w.finding(conn, self.project, self.session, self.finding_spec(**extra))

    def test_duplicate_cannot_silently_replace_evidence(self):
        self.start()
        with w.database(self.db) as conn:
            original = w.finding(conn, self.project, self.session, self.finding_spec())
        with self.assertRaises(ValueError), w.database(self.db) as conn:
            w.finding(conn, self.project, self.session, self.finding_spec(title='Different title'))
        with w.database(self.db) as conn:
            item = w.finding(conn, self.project, self.session, self.finding_spec(status='rejected', replace=True))
            self.assertEqual(original['id'], item['id'])
            self.assertEqual('validated', item['history'][0]['status'])

    def test_changed_or_missing_evidence_downgrades_export(self):
        self.start()
        with w.database(self.db) as conn:
            w.finding(conn, self.project, self.session, self.finding_spec())
            run = w.current(conn, self.project, self.session)
        self.assertEqual('validated', w.report_data(run)['findings'][0]['status'])
        self.proof.write_text('changed', encoding='utf-8')
        self.assertEqual('inconclusive', w.report_data(run)['findings'][0]['status'])
        self.proof.unlink()
        self.assertEqual('inconclusive', w.report_data(run)['findings'][0]['status'])
        self.assertEqual('validated', run['findings'][0]['status'])

    def test_changed_review_downgrades_export(self):
        self.start()
        with w.database(self.db) as conn:
            w.finding(conn, self.project, self.session, self.finding_spec())
            run = w.current(conn, self.project, self.session)
        self.review_file.write_text('changed review', encoding='utf-8')
        self.assertEqual('inconclusive', w.report_data(run)['findings'][0]['status'])

    def test_changed_subject_downgrades_export(self):
        self.start()
        with w.database(self.db) as conn:
            w.finding(conn, self.project, self.session, self.finding_spec())
            run = w.current(conn, self.project, self.session)
        (self.root / 'subject.py').write_text('print("new version")', encoding='utf-8')
        self.assertEqual('inconclusive', w.report_data(run)['findings'][0]['status'])

    def test_candidates_are_separate_from_validated_findings(self):
        self.start()
        with w.database(self.db) as conn:
            w.finding(conn, self.project, self.session, dict(title='Unproven', asset='fixture', root_cause='Unknown', status='candidate'))
            report = w.markdown(w.report_data(w.current(conn, self.project, self.session)))
        self.assertIn('Validated findings (0)', report)
        self.assertIn('Candidate findings (1)', report)

    def test_evidence_cannot_escape_project(self):
        with self.assertRaises(ValueError):
            w.evidence_records(self.project, [str(self.proof)])
        child = self.root / 'child'
        child.mkdir()
        with self.assertRaises(ValueError):
            w.evidence_records(str(child), ['../proof.txt'])

    def test_symlink_cannot_escape_project(self):
        child = self.root / 'child'
        child.mkdir()
        try:
            (child / 'link').symlink_to(self.proof)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaises(ValueError):
            w.evidence_records(str(child), ['link'])

    def test_cli_persistence_in_separate_process(self):
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps(self.plan), encoding='utf-8')
        prefix = [sys.executable, str(w.ROOT / 'workflow.py'), '--db', str(self.db), '--cwd', self.project, '--session', self.session]
        a = subprocess.run(prefix + ['start', '--file', str(plan)], capture_output=True, text=True)
        self.assertEqual(0, a.returncode, a.stderr)
        b = subprocess.run(prefix + ['status'], capture_output=True, text=True)
        self.assertEqual(json.loads(a.stdout)['run_id'], json.loads(b.stdout)['id'])

    def test_malformed_hook_fails_open_without_raw_input(self):
        result = subprocess.run([sys.executable, str(w.ROOT / 'workflow.py'), '--db', str(self.db), 'hook'],
                                input='not valid json FAKE-SECRET', capture_output=True, text=True)
        self.assertEqual(0, result.returncode)
        self.assertIn('systemMessage', json.loads(result.stdout))
        self.assertNotIn('FAKE-SECRET', result.stdout + result.stderr)

    def test_invalid_update_is_atomic(self):
        self.start()
        with self.assertRaises(ValueError), w.database(self.db) as conn:
            w.checkpoint(conn, self.project, self.session, dict(summary='bad', next_action='bad', tasks=[
                dict(id='1', status='running'), dict(id='missing', status='running')]))
        with w.database(self.db) as conn:
            self.assertEqual('pending', w.current(conn, self.project, self.session)['tasks'][0]['status'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
