from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BlacklistCreateRequest(BaseModel):
    type: Literal["email", "uid", "ip"]
    value: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=255)
    comment: str | None = Field(default=None, max_length=512)

    model_config = ConfigDict(extra="forbid")


class BlacklistCreatedResponse(BaseModel):
    id: str
    detail: str = "added"


class MessageResponse(BaseModel):
    detail: str
