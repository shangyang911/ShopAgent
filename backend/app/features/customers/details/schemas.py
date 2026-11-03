"""标签去空格、去重并限制长度；版本号避免覆盖他人的跟进修改。"""
from pydantic import BaseModel, Field, field_validator


class ProfileWrite(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=5000)
    expected_revision: int = Field(ge=0)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values):
        values = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(value) > 30 for value in values):
            raise ValueError("每个标签最多 30 个字符")
        return values
