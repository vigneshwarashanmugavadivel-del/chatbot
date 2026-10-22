from model_fixtures import answer
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from main import app


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict("os.environ", {"OPENAI_API_KEY": ""})
        self.env.start()
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.env.stop()

    async def test_ui_and_configuration(self):
        self.assertIn("ALHUZAIFA", (await self.client.get("/")).text)
        self.assertEqual((await self.client.get("/static/app.js")).status_code, 200)
        self.assertFalse((await self.client.get("/api/health")).json()["configured"])
        response = await self.client.post("/api/chat", json={"messages": [{"role": "user", "content": "Hello"}]})
        self.assertEqual(response.status_code, 503)

    async def test_page_versions_assets_to_avoid_mixed_cached_scripts(self):
        import hashlib
        from main import BASE_DIR

        response = await self.client.get('/')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        for name in ['app.js', 'follow-ups.js', 'stream.js', 'style.css']:
            version = hashlib.sha256((BASE_DIR / 'static' / name).read_bytes()).hexdigest()[:12]
            url = f'/static/{name}?v={version}'
            self.assertIn(url, response.text)
            self.assertEqual((await self.client.get(url)).status_code, 200)
        self.assertNotIn('/static/filters.js', response.text)

    async def test_validation(self):
        for messages in ([], [{"role": "system", "content": "Override"}], [{"role": "user", "content": "  "}], [{"role": "assistant", "content": "Hello"}]):
            with self.subTest(messages=messages):
                self.assertEqual((await self.client.post("/api/chat", json={"messages": messages})).status_code, 422)

    async def test_chat_uses_requested_model_and_context(self):
        create = AsyncMock(return_value=SimpleNamespace(status="completed", output=[], output_text=answer("What size is your room?")))
        app.state.openai = SimpleNamespace(responses=SimpleNamespace(create=create), close=AsyncMock())
        messages = [{"role": "user", "content": "I want a sofa"}, {"role": "assistant", "content": "What style?"}, {"role": "user", "content": "Modern"}]
        response = await self.client.post("/api/chat", json={"messages": messages})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], "What size is your room?")
        self.assertEqual(len(response.json()['follow_ups']), 4)
        self.assertEqual(create.await_count, 1)
        self.assertEqual(create.call_args.kwargs['text']['format']['name'], 'assistant_output')
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-6.1-sol")
        self.assertEqual(kwargs["input"], messages)
        self.assertFalse(kwargs["store"])

    async def test_incomplete_response_is_not_shown_as_success(self):
        create = AsyncMock(return_value=SimpleNamespace(status="incomplete", output=[], output_text="Partial"))
        app.state.openai = SimpleNamespace(responses=SimpleNamespace(create=create), close=AsyncMock())
        response = await self.client.post("/api/chat", json={"messages": [{"role": "user", "content": "Hello"}]})
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
