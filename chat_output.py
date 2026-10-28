"""One structured model response with independently streamed reply text."""

import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from follow_ups import FollowUps, Question


class AssistantOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reply: str = Field(min_length=1)
    follow_ups: list[Question] = Field(
        min_length=4, max_length=4,
        description='Four distinct suggested customer messages, with content determined by the chat instructions.')

    @field_validator('reply')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Reply must not be blank')
        return value

    @field_validator('follow_ups')
    @classmethod
    def distinct(cls, value):
        return FollowUps(questions=value).questions


class ReplyTextStream:
    """Decode only the leading reply string from partial JSON, never metadata.

    Structured output follows schema property order. Keep incomplete escapes and
    surrogate pairs buffered until their complete character is available.
    """

    def __init__(self):
        self.buffer = ''
        self.text = ''

    def feed(self, chunk):
        self.buffer += chunk
        match = re.match(r'^\s*\{\s*"reply"\s*:\s*"', self.buffer)
        if not match:
            return ''
        source = self.buffer[match.end():]
        characters = []
        index = 0
        while index < len(source):
            character = source[index]
            if character == '"':
                break
            if character != '\\':
                characters.append(character)
                index += 1
                continue
            if index + 1 >= len(source):
                break
            length = 6 if source[index + 1] == 'u' else 2
            if index + length > len(source):
                break
            if length == 6 and 0xD800 <= int(source[index + 2:index + 6], 16) <= 0xDBFF:
                if index + 12 > len(source):
                    break
                if source[index + 6:index + 8] == '\\u':
                    length = 12
            characters.append(json.loads('"' + source[index:index + length] + '"'))
            index += length
        decoded = ''.join(characters)
        if not decoded.startswith(self.text):
            raise ValueError('Reply stream changed an already emitted prefix')
        delta = decoded[len(self.text):]
        self.text = decoded
        return delta
