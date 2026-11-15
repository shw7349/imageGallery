from typing import Annotated

from pydantic import Field, StringConstraints

from app.schemas.base import CamelModel

CategoryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class CategoryIn(CamelModel):
    name: CategoryName = Field(examples=["풍경"])
    sort_order: int = Field(default=0, ge=0, le=1_000_000, description="작을수록 앞", examples=[0])


class CategoryOut(CamelModel):
    id: int = Field(examples=[1])
    name: str = Field(examples=["풍경"])
    sort_order: int = Field(examples=[0])
