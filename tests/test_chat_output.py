import json
import unittest

from chat_output import AssistantOutput, ReplyTextStream
from model_fixtures import answer


class ChatOutputTests(unittest.TestCase):
    def test_streams_only_reply_when_json_is_split_at_every_character(self):
        reply = 'A "soft" finish.\nCafé, العربية, and a smile: 😀. A backslash: \\.'
        raw = answer(reply)
        stream = ReplyTextStream()
        emitted = ''.join(stream.feed(character) for character in raw)
        self.assertEqual(emitted, reply)
        self.assertNotIn('follow_ups', emitted)
        self.assertEqual(AssistantOutput.model_validate_json(raw).reply, emitted)

    def test_questions_never_leak_after_reply_is_complete(self):
        stream = ReplyTextStream()
        self.assertEqual(stream.feed('{"reply":"Hello'), 'Hello')
        self.assertEqual(stream.feed(' there", "follow_ups": ["Question?"'), ' there')
        self.assertEqual(stream.feed(', "More?", "Other?", "Details?"]}'), '')

    def test_incomplete_unicode_and_escaped_quotes_are_buffered(self):
        stream = ReplyTextStream()
        self.assertEqual(stream.feed('{"reply":"A \\u00'), 'A ')
        self.assertEqual(stream.feed('e9 \\'), 'é ')
        self.assertEqual(stream.feed('"word\\"'), '"word"')

    def test_schema_has_reply_first_and_requires_four_questions(self):
        schema = AssistantOutput.model_json_schema()
        self.assertEqual(list(schema['properties']), ['reply', 'follow_ups'])
        self.assertEqual(schema['properties']['follow_ups']['minItems'], 4)
        self.assertEqual(schema['properties']['follow_ups']['maxItems'], 4)
