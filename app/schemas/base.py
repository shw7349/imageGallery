from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """API 스키마 기반: JSON은 camelCase, 파이썬은 snake_case. 요청은 두 형식 모두 받는다."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)
