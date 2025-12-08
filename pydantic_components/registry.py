from collections.abc import Mapping
from functools import cached_property
from typing import Any, Self, cast

from pydantic import Field, PrivateAttr

from .component import BaseComponent
from .exceptions import ComponentNotFoundError
from .provider import BaseProvider, ValidationContext
from .resolver import ComponentContext
from .utils import RecursionGuard


class ComponentRegistry[ComponentT: BaseComponent](
    BaseProvider[ComponentT],
    frozen=True,
):
    components: list[ComponentT] = Field(default=[])
    validation_context: Mapping[str, Any] = Field(default={})
    _providers: list[BaseProvider[ComponentT]] = PrivateAttr(default=[])

    @cached_property
    def root_context(self) -> ComponentContext[Self, ComponentT]:
        return ComponentContext(
            self,
            None,
            cast("ValidationContext", self.validation_context),
        )

    async def __aenter__(self) -> Self:
        for component in self.components:
            self.root_context._load(component)  # noqa: SLF001
        await self.root_context.__aenter__()
        for component in self.components:
            if isinstance(component, BaseProvider):
                await self._resolve_provider(component)
        return self

    async def _resolve_provider(
        self,
        provider: BaseProvider[ComponentT],
        *,
        _recursion_guard: RecursionGuard | None = None,
    ) -> None:
        _recursion_guard = _recursion_guard or RecursionGuard(
            "Maximum provider resolution recursion exceeded",
            5,
        )
        _recursion_guard.increment()

        await self.root_context._resolve_all()  # noqa: SLF001
        await provider.__aenter__()
        if issubclass(provider.provides_type, BaseProvider):
            async for sub_provider in provider.list():
                await self._resolve_provider(
                    cast("BaseProvider[ComponentT]", sub_provider),
                    _recursion_guard=_recursion_guard.copy(),
                )
        self._providers.append(provider)

    async def __aexit__(self, *exc_args: object) -> bool | None:
        for provider in self._providers:
            await provider.__aexit__(*exc_args)
        return None

    def provides_uri(self, uri: str) -> bool:
        return (
            uri in self.root_context._resolved  # noqa: SLF001
            or any(p.provides_uri(uri) for p in self._providers)
        )

    async def get(
        self,
        uri: str,
        context: ValidationContext | None = None,
    ) -> ComponentT:
        if uri in self.root_context._resolved:  # noqa: SLF001
            return self.root_context._resolved[uri]  # noqa: SLF001

        for provider in self._providers:
            if provider.provides_uri(uri):
                return await provider.get(uri, context)

        raise ComponentNotFoundError(uri, "No providers found")
