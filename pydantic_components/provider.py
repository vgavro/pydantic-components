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

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        super().__pydantic_init_subclass__(**kwargs)
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


class RegistryProvider[ComponentT: BaseComponent](
    BaseProvider[ComponentT],
    frozen=True,
):
    components: list[ComponentT]

    def __init__(self, components: list[object], **kwargs: object) -> None:
        super().__init__(components=components, **kwargs)  # type: ignore[reportIncompatibleVariableOverride]

    @cached_property
    def components_map(self) -> dict[str, ComponentT]:
        return {c.uri: c for c in self.components}

    def provides_uri(self, uri: str) -> bool:
        return uri in self.components_map

    async def get(
        self,
        uri: str,
        context: ValidationContext | None = None,
    ) -> ComponentT:
        if not self.provides_uri(uri):
            raise ComponentNotFoundError(uri, "Component not found")
        # NOTE: for components with ComponentUri we need
        # to re-validate model with registry context,
        # te solve this we may either:
        # * force it with model_dump/model_validate here
        # * use `class Model(BaseModel, revalidate_instances='always')`
        #   https://docs.pydantic.dev/2.1/usage/model_config/#revalidate-instances
        # * accept raw objects to validate later in `components` on initialzation
        return self.components_map[uri].model_validate(
            self.components_map[uri],
            context=context,
        )

    async def list_cursored(
        self,
        cursor: str | None = None,
        context: ValidationContext | None = None,
    ) -> tuple[Iterable[ComponentT], str | None]:
        _, _ = cursor, context
        return [c.model_validate(c, context=context) for c in self.components], None
