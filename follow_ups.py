"""Validation for model-generated follow-up questions."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]


class FollowUps(BaseModel):
    model_config = ConfigDict(extra='forbid')
    questions: list[Question] = Field(min_length=4, max_length=4)

    @field_validator('questions')
    @classmethod
    def distinct(cls, questions):
        if len({question.casefold() for question in questions}) != 4:
            raise ValueError('Follow-up questions must be distinct')
        return questions
