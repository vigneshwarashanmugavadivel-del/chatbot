import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from pydantic import ValidationError

from main import (
    AGENT_INSTRUCTIONS, CHAT_INSTRUCTIONS, MAX_CHAT_INSTRUCTIONS_LENGTH,
    ChatRequest, app, chat_events,
)
from model_fixtures import answer
from test_streaming import FakeStream


class ChatInstructionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_editor_loads_chat_defaults_without_agent_instructions(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/chat/instructions')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'chat_instructions': CHAT_INSTRUCTIONS, 'max_length': MAX_CHAT_INSTRUCTIONS_LENGTH,
        })
        self.assertNotIn(AGENT_INSTRUCTIONS, response.text)

    async def test_custom_chat_instructions_reach_model_in_both_modes(self):
        for streaming in (False, True):
            response = SimpleNamespace(status='completed', output=[], output_text=answer('Hello.'))
            returned = FakeStream([SimpleNamespace(type='response.completed', response=response)]) if streaming else response
            create = AsyncMock(return_value=returned)
            payload = ChatRequest(messages=[{'role': 'user', 'content': 'Hello'}],
                                  chat_instructions='  Reply in short Hindi sentences.  ',
                                  search_state={'query': 'sofa', 'filters': {'colors': ['beige']}})
            events = [event async for event in chat_events(
                payload, SimpleNamespace(responses=SimpleNamespace(create=create)), None, streaming)]
            instructions = create.call_args.kwargs['instructions']
            self.assertIn(AGENT_INSTRUCTIONS, instructions)
            self.assertIn('Chat instructions:\nReply in short Hindi sentences.', instructions)
            self.assertNotIn(CHAT_INSTRUCTIONS, instructions)
            self.assertIn('Active search context', instructions)
            self.assertEqual(events[-1]['type'], 'done')

    async def test_omitting_override_uses_server_defaults(self):
        create = AsyncMock(return_value=SimpleNamespace(status='completed', output=[], output_text=answer('Hello.')))
        payload = ChatRequest(messages=[{'role': 'user', 'content': 'Hello'}])
        _ = [event async for event in chat_events(payload, SimpleNamespace(responses=SimpleNamespace(create=create)), None)]
        self.assertIn(CHAT_INSTRUCTIONS, create.call_args.kwargs['instructions'])
        self.assertIn(AGENT_INSTRUCTIONS, create.call_args.kwargs['instructions'])

    def test_instruction_validation(self):
        for value in ['', '   ', 'x' * (MAX_CHAT_INSTRUCTIONS_LENGTH + 1)]:
            with self.subTest(value_length=len(value)), self.assertRaises(ValidationError):
                ChatRequest(messages=[{'role': 'user', 'content': 'Hello'}], chat_instructions=value)
