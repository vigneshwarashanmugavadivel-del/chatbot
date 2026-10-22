from model_fixtures import answer
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from main import ChatRequest, chat_events
from product_search import Product, SearchResult
from test_streaming import FakeStream


def tool_response(index, arguments=None):
    call = SimpleNamespace(type='function_call', name='search_products', call_id=f'call{index}',
                           arguments=json.dumps(arguments or {'query':f'attempt {index}'}))
    return SimpleNamespace(status='completed', output=[call], output_text='')


def final_response():
    return SimpleNamespace(status='completed', output=[], output_text=answer('Here is what I found.'))


class AgenticTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_can_investigate_beyond_two_tool_calls(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                reasoning = SimpleNamespace(type='reasoning', encrypted_content='opaque-continuation')
                display = tool_response(5)
                display.output[0].name = 'display_products'
                display.output[0].arguments = json.dumps({'presentation':'recommendations', 'products':[
                    {'retrieval_id':'retrieval_5','product_id':'FOUND'}]})
                responses = [tool_response(index) for index in range(5)] + [display, final_response()]
                responses[0].output.insert(0, reasoning)
                returned = [FakeStream([SimpleNamespace(type='response.completed', response=item)])
                            for item in responses] if streaming else responses
                create = AsyncMock(side_effect=returned)
                client = SimpleNamespace(responses=SimpleNamespace(create=create))
                results = [SearchResult(query=f'attempt {index}', products=[], total=0, page=1, pages=0)
                           for index in range(4)] + [SearchResult(query='attempt 4', products=[
                               Product(id='FOUND', title='Found', stock=2)], total=1, page=1, pages=1)]
                searcher = SimpleNamespace(search=AsyncMock(side_effect=results))
                events = [event async for event in chat_events(ChatRequest(messages=[
                    {'role':'user','content':'Find the products I asked about.'}]), client, searcher, streaming)]
                self.assertEqual(create.await_count, 7)
                self.assertEqual(searcher.search.await_count, 5)
                self.assertTrue(all(args.kwargs['tool_choice'] == 'auto' for args in create.call_args_list))
                self.assertIn(reasoning, create.call_args_list[-1].kwargs['input'])
                outputs = [item for item in create.call_args_list[-1].kwargs['input']
                           if isinstance(item, dict) and item.get('type') == 'function_call_output']
                self.assertEqual(len(outputs), 6)
                self.assertEqual(events[-1]['data']['presentation']['items'][0]['product']['id'], 'FOUND')

    async def test_model_can_repair_invalid_arguments_and_continue(self):
        create = AsyncMock(side_effect=[tool_response(0, {'query':'test','limit':100}),
                                        tool_response(1), final_response()])
        searcher = SimpleNamespace(search=AsyncMock(return_value=SearchResult(
            query='attempt 1', products=[], total=0, page=1, pages=0)))
        events = [event async for event in chat_events(ChatRequest(messages=[
            {'role':'user','content':'Find something for me.'}]),
            SimpleNamespace(responses=SimpleNamespace(create=create)), searcher)]
        self.assertEqual(create.await_count, 3)
        self.assertEqual(searcher.search.await_count, 1)
        output = create.call_args_list[1].kwargs['input'][-1]
        self.assertIn('error', json.loads(output['output']))
        self.assertEqual(events[-1]['type'], 'done')
