from collections.abc import AsyncIterable, Iterable
from functools import cached_property
from typing import TYPE_CHECKING, Any, ClassVar, NotRequired, Self, TypedDict

from pydantic import TypeAdapter
from pydantic._internal import _generics as pydantic_generics

from .component import BaseComponent
from .exceptions import ComponentNotFoundError

if TYPE_CHECKING:
    from .resolver import ComponentContext


class ValidationContext(TypedDict, total=False):
    component_context: NotRequired["ComponentContext"]


class BaseProvider[ComponentT: BaseComponent](BaseComponent, frozen=True):
    # ComponentT can't be used with type[] for whatever reason,
    # so using BaseComponent here
    provides_type: ClassVar[type[ComponentT]]  # type: ignore[reportGeneralTypeIssues]

    def provides_uri(self, uri: str) -> bool:
        _ = uri
        return False

    @classmethod
    def __resolve_provides_type(cls) -> type[ComponentT] | None:
        base_args = pydantic_generics.get_args(cls)
        if base_args:
            return base_args[0]
        return None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        provides_type = cls.__resolve_provides_type()
        if provides_type:
            cls.provides_type = provides_type

    @cached_property
    def provides_type_adapter(self) -> TypeAdapter[ComponentT]:
        return TypeAdapter(self.provides_type)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_args: Any) -> bool | None:
        _ = exc_args
        return None

    async def get(
        self,
        uri: str,
        context: ValidationContext | None = None,
    ) -> ComponentT:
        async for component in self.list(context):
            assert hasattr(component, "uri")
            if component.uri == uri:
                return component
        raise ComponentNotFoundError(uri, "Component not found")

    async def list(
        self,
        context: ValidationContext | None = None,
    ) -> AsyncIterable[ComponentT]:
        cursor: str | None = None
        while True:
            components, cursor = await self.list_cursored(cursor, context)
            for component in components:
                yield component
            if not cursor:
                break

    async def list_cursored(
        self,
        cursor: str | None = None,
        context: ValidationContext | None = None,
    ) -> tuple[Iterable[ComponentT], str | None]:
        _, _ = cursor, context
        return [], None
