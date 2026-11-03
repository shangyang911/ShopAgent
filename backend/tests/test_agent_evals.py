"""Scoring must reject broken behavior, not just replay successful golden answers."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals.cases import cases
from evals.runner import score, run_case, aggregate, write_reports, ReplayProvider


class EvalTests(unittest.TestCase):
    def case(self, name):
        return next(c for c in cases() if c['name'] == name)

    def test_task_ids_are_unique_and_sixteen_scenarios_are_explicit(self):
        tasks = cases()
        self.assertEqual(len(tasks), 80)
        self.assertEqual(len({c['id'] for c in tasks}), 80)
        self.assertEqual(len({c['name'] for c in tasks}), 16)

    def test_wrong_order_and_missing_tool_fail_despite_correct_words(self):
        case = self.case('detail')
        answer = {'text': 'EVAL-001 金额20.00'}
        checks = score(case, {'calls': []}, answer, {}, {}, [], None)
        self.assertFalse(checks['tool_selection'])
        self.assertFalse(checks['tool_arguments'])
        checks = score(case, {'calls': [{'name': 'get_order', 'arguments': {'order_id': 2}, 'status': 'ok'}]},
                       answer, {}, {}, [], None)
        self.assertFalse(checks['tool_arguments'])

    def test_unconfirmed_write_and_unwanted_action_are_rejected(self):
        case = self.case('reason_missing')
        checks = score(case, {'calls': []}, {'text': '请提供原因', 'action': {'id': 'x'}},
                       {'stock': 6}, {'stock': 8}, ['pending'], None)
        self.assertFalse(checks['no_unconfirmed_write'])
        self.assertFalse(checks['action_contract'])

    def test_policy_needs_citation_and_refund_claim_is_rejected(self):
        case = self.case('policy_hit')
        checks = score(case, {'calls': []}, {'text': '15天内申请'}, {}, {}, [], None)
        self.assertFalse(checks['answer_assertions'])
        checks = score(self.case('unsupported'), {'calls': []}, {'text': '无法自动处理，但已退款'}, {}, {}, [], None)
        self.assertFalse(checks['answer_assertions'])

    def test_actual_engine_creates_preview_without_mutating_inventory(self):
        row = asyncio.run(run_case(self.case('cancel'), 'offline', None, 10))
        self.assertTrue(row['passed'], row)
        self.assertEqual(row['answer']['action']['state'], 'pending')

    def test_broken_provider_is_visible_as_a_failed_task(self):
        async def wrong_answer(*args):
            return {'content': json.dumps({'text': '已退款成功', 'speech_text': '退款成功'})}
        with patch.object(ReplayProvider, 'complete', wrong_answer):
            row = asyncio.run(run_case(self.case('unsupported'), 'offline', None, 10))
        self.assertFalse(row['passed'])
        self.assertFalse(row['checks']['answer_assertions'])

    def test_report_escapes_untrusted_output_and_usage_is_unknown(self):
        row = asyncio.run(run_case(self.case('detail'), 'offline', None, 10))
        summary = aggregate([row])
        self.assertIsNone(summary['total_tokens'])
        self.assertIsNone(summary['cost'])
        row['prompt'] = '<script>bad()</script>'
        report = {'run_id': 'test', 'mode': 'offline', 'summary': summary, 'results': [row]}
        with tempfile.TemporaryDirectory() as folder:
            path = write_reports(report, Path(folder))
            page = path.read_text(encoding='utf-8')
            self.assertNotIn('<script>', page)
            self.assertIn('&lt;script&gt;', page)


if __name__ == '__main__':
    unittest.main()
