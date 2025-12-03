from pydantic import BaseModel


class BaseComponent(BaseModel, frozen=True):
    @property
    def uri(self) -> str: ...
