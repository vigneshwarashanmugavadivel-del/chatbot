import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from agent_logging import AgentTrace, TraceWriter, ACTIVE_TRACE, log_es_request, log_es_response
from main import ChatRequest, chat_events
from model_fixtures import answer


class LoggingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'agent.jsonl'
        self.writer = TraceWriter(self.path)
        self.addCleanup(self.writer.close)

    def records(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    async def test_timestamps_use_ist_in_both_log_formats(self):
        AgentTrace(self.writer).emit('test')
        timestamp = self.records()[0]['timestamp']
        self.assertEqual(datetime.fromisoformat(timestamp).utcoffset(), timedelta(hours=5, minutes=30))
        readable_timestamp = datetime.fromisoformat(timestamp).isoformat(sep=' ', timespec='seconds')
        self.assertIn(readable_timestamp, self.writer.readable_path.read_text())

    async def test_readable_timestamps_have_spaces_and_durations_are_rounded(self):
        self.writer.write({'timestamp':'2026-10-22T16:05:50.123456+05:30',
                           'event':'model.completed', 'duration_ms':1234.78})
        readable = self.writer.readable_path.read_text()
        self.assertIn('2026-10-22 16:05:50+05:30', readable)
        self.assertIn('1235 ms', readable)
        self.assertNotIn('.123456', readable)
        self.assertEqual(self.records()[0]['duration_ms'], 1234.78)

    async def test_nested_credentials_and_reasoning_are_not_written(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY':'configured-private-key', 'ES_PASSWORD':'configured-password'}):
            trace = AgentTrace(self.writer)
            trace.emit('test', arguments=json.dumps({'ES_API_KEY':'unconfigured-secret', 'nested':{'password':'other-password'}}),
                       messages=['configured-private-key', 'configured-password', 'Authorization: Bearer secret-token',
                                 'https://username:url-password@example.com'],
                       input=[SimpleNamespace(type='reasoning', encrypted_content='hidden-reasoning')])
        data = self.path.read_text() + self.writer.readable_path.read_text()
        for value in ['configured-private-key','configured-password','unconfigured-secret','other-password',
                      'secret-token','url-password','hidden-reasoning']:
            self.assertNotIn(value, data)
        self.assertEqual(self.records()[0]['input'][0]['content'], '[OMITTED]')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.writer.readable_path.stat().st_mode & 0o777, 0o600)

    async def test_readable_log_summarizes_steps_without_catalog_payloads(self):
        trace = AgentTrace(self.writer)
        trace.emit('tool.completed', tool='search_products', duration_ms=12,
                   result={'products':[{'title':'Café table', 'price':200}]})
        trace.emit('turn.completed', result={'reply':'Here are some options.\nTake a look.'})
        readable = self.writer.readable_path.read_text()
        self.assertIn('Step 1 | Tool / Completed', readable)
        self.assertIn('Step 2 | Turn / Completed', readable)
        self.assertEqual(readable.count(f'[{trace.trace_id}]'), 2)
        self.assertNotIn('Café table', readable)
        self.assertIn('1 products', readable)
        self.assertIn('Reply: Here are some options. Take a look.', readable)
        self.assertIn('12 ms', readable)
        self.assertEqual(len(readable.splitlines()), 2)

    async def test_streaming_noise_stays_only_in_jsonl_and_previews_are_bounded(self):
        trace = AgentTrace(self.writer)
        trace.emit('ui.delta', characters=12)
        trace.emit('ui.event', data={'type':'status'})
        trace.emit('turn.closed', outcome='completed')
        trace.emit('turn.completed', result={'reply':'x' * 10000})
        readable = self.writer.readable_path.read_text()
        self.assertEqual(len(self.records()), 4)
        self.assertEqual(len(readable.splitlines()), 1)
        self.assertLess(len(readable), 600)
        self.assertIn('…', readable)

    async def test_each_model_round_includes_complete_visible_output(self):
        trace = AgentTrace(self.writer)
        with patch.dict(os.environ, {'OPENAI_API_KEY':'private-test-key'}):
            trace.emit('model.completed', round=1, output=[
                {'type':'reasoning', 'content':'private-thoughts'},
                {'type':'function_call', 'name':'search_products', 'call_id':'call1',
                 'arguments':json.dumps({'query':'table', 'api_key':'private-test-key'})},
                {'type':'function_call', 'name':'update_preferences', 'call_id':'call2',
                 'arguments':json.dumps({'search_state':None})},
            ], output_text='')
            reply = {'reply':'A long reply: ' + 'x' * 600, 'follow_ups':['First?', 'Second?']}
            trace.emit('model.completed', round=2, output=[
                {'type':'message', 'content':[{'type':'output_text', 'text':json.dumps(reply)}]},
            ], output_text=json.dumps(reply))
        readable = self.writer.readable_path.read_text()
        outputs = [json.loads(line.removeprefix('  LLM output: '))
                   for line in readable.splitlines() if line.startswith('  LLM output: ')]
        self.assertEqual(len(outputs), 3)
        self.assertEqual(outputs[0]['tool'], 'search_products')
        self.assertEqual(outputs[0]['arguments'], {'query':'table', 'api_key':'[REDACTED]'})
        self.assertEqual(outputs[1]['tool'], 'update_preferences')
        self.assertEqual(outputs[2], reply)
        self.assertNotIn('private-test-key', readable)
        self.assertNotIn('private-thoughts', readable)
        self.assertIn('round 1', readable)
        self.assertIn('round 2', readable)

    async def test_existing_jsonl_is_converted_once_without_modification(self):
        path = Path(self.directory.name) / 'existing.jsonl'
        original = json.dumps({'step':1, 'event':'turn.completed', 'reply':'Hello', 'password':'private'}) + '\n'
        path.write_text(original + '{partial', encoding='utf-8')
        for _ in range(2):
            writer = TraceWriter(path)
            writer.close()
        readable = path.with_suffix('.log').read_text()
        self.assertEqual(readable.count('Step 1 | Turn / Completed'), 1)
        self.assertIn('Hello', readable)
        self.assertNotIn('private', readable)
        self.assertEqual(path.read_text(), original + '{partial')

    async def test_log_suffix_cannot_overwrite_structured_log(self):
        path = Path(self.directory.name) / 'custom.log'
        writer = TraceWriter(path)
        try:
            writer.write({'step':1, 'event':'test'})
            self.assertNotEqual(writer.readable_path, path)
            self.assertEqual(json.loads(path.read_text())['event'], 'test')
        finally:
            writer.close()

    async def test_success_records_model_tools_presentation_and_final_response(self):
        trace = AgentTrace(self.writer)
        calls = [SimpleNamespace(type='function_call', name='update_preferences', call_id='prefs1',
                                 arguments='{"search_state":{"query":"sofa"}}')]
        create = AsyncMock(side_effect=[SimpleNamespace(status='completed', output=calls, output_text=''),
                                       SimpleNamespace(status='completed', output=[], output_text=answer('Ready.'))])
        with patch('main.AgentTrace', return_value=trace):
            events = [event async for event in chat_events(ChatRequest(messages=[{'role':'user','content':'Find a sofa.'}]),
                SimpleNamespace(responses=SimpleNamespace(create=create)), None)]
        records = self.records()
        names = [record['event'] for record in records]
        for event in ['turn.started','model.request','model.completed','tool.started','tool.completed','turn.completed','turn.closed']:
            self.assertIn(event, names)
        self.assertEqual(events[0]['trace_id'], trace.trace_id)
        self.assertEqual(events[-1]['data']['trace_id'], trace.trace_id)
        self.assertEqual({record['trace_id'] for record in records}, {trace.trace_id})
        self.assertEqual([record['step'] for record in records], list(range(1, len(records)+1)))
        tool = next(record for record in records if record['event']=='tool.completed')
        self.assertEqual(tool['call_id'], 'prefs1')
        self.assertEqual(tool['result']['search_state']['query'], 'sofa')
        self.assertGreaterEqual(tool['duration_ms'], 0)
        self.assertEqual(records[-1]['outcome'], 'completed')

    async def test_tool_validation_failure_is_logged_and_can_be_repaired(self):
        trace = AgentTrace(self.writer)
        call = SimpleNamespace(type='function_call', name='display_products', call_id='bad1',
                               arguments='{"presentation":"recommendations","products":[]}')
        create = AsyncMock(side_effect=[SimpleNamespace(status='completed', output=[call], output_text=''),
                                       SimpleNamespace(status='completed', output=[], output_text=answer('No exact match.'))])
        with patch('main.AgentTrace', return_value=trace):
            _ = [event async for event in chat_events(ChatRequest(messages=[{'role':'user','content':'Find a product.'}]),
                SimpleNamespace(responses=SimpleNamespace(create=create)), None)]
        records = self.records()
        self.assertTrue(any(record['event']=='tool.failed' and record['call_id']=='bad1' for record in records))
        self.assertFalse(next(record for record in records if record['event']=='tool.completed')['success'])
        self.assertEqual(records[-1]['outcome'], 'completed')

    async def test_elasticsearch_query_and_response_share_tool_and_request_ids(self):
        trace = AgentTrace(self.writer)
        async def handler(request):
            return httpx.Response(200, json={'responses':[{'hits':{'hits':[]}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), event_hooks={
            'request':[log_es_request], 'response':[log_es_response],
        }) as client:
            with trace.activate(round=2, call_id='search1', tool='search_products'):
                response = await client.post('https://example.com/_msearch', content='{"index":"products"}\n{"query":{"match_all":{}}}\n')
                self.assertEqual(response.json()['responses'][0]['hits']['hits'], [])
        records = self.records()
        self.assertEqual([record['event'] for record in records], ['elasticsearch.request','elasticsearch.response'])
        self.assertEqual(records[0]['request_id'], records[1]['request_id'])
        self.assertEqual(records[0]['call_id'], 'search1')
        self.assertEqual(records[0]['body'][1]['query'], {'match_all':{}})
        self.assertIsNone(ACTIVE_TRACE.get())

    async def test_failure_and_early_close_are_terminally_logged(self):
        trace = AgentTrace(self.writer)
        client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
            status='completed', output=[], output_text='not structured JSON'))))
        with patch('main.AgentTrace', return_value=trace):
            with self.assertRaises(Exception):
                _ = [event async for event in chat_events(ChatRequest(messages=[{'role':'user','content':'Hello'}]),client,None)]
        self.assertEqual(self.records()[-1]['outcome'], 'failed')
        cancelled = AgentTrace(self.writer)
        with patch('main.AgentTrace', return_value=cancelled):
            generator = chat_events(ChatRequest(messages=[{'role':'user','content':'Hello'}]),client,None)
            await anext(generator)
            await generator.aclose()
        self.assertEqual(self.records()[-1]['outcome'], 'cancelled')
        self.assertTrue(any(record['event']=='turn.cancelled' for record in self.records()))

    async def test_rotation_keeps_valid_json_lines_and_private_files(self):
        path = Path(self.directory.name) / 'rotating.jsonl'
        writer = TraceWriter(path, max_bytes=400, backups=2)
        try:
            for index in range(50):
                writer.write({'step':index,'event':'test','content':'x'*100})
        finally:
            writer.close()
        files = list(path.parent.glob('rotating.jsonl*'))
        self.assertEqual(len(files), 3)
        readable_files = list(path.parent.glob('rotating.log*'))
        self.assertEqual(len(readable_files), 3)
        for file in readable_files:
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertIn('| Test', file.read_text())
        for file in files:
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            for line in file.read_text().splitlines():
                self.assertEqual(json.loads(line)['event'], 'test')
