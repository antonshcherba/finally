from typing import Literal

from pydantic import BaseModel, Field


class LLMAction(BaseModel):
    type: Literal["buy", "sell", "watch_add", "watch_remove"]
    ticker: str
    quantity: float | None = None


class LLMResponse(BaseModel):
    message: str
    actions: list[LLMAction] = Field(default_factory=list)
