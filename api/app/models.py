from uuid import UUID

from pydantic import BaseModel, Field


class SpaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class SpacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class AskIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    document_ids: list[UUID] | None = None


class ConversationPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)
