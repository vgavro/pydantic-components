from collections.abc import Iterable
from typing import Any, Self

from pydantic import PrivateAttr

from .component import BaseComponent
from .provider import BaseProvider, ValidationContext
from .resolver import ComponentResolutionContext
from .utils import RecursionGuard

__version__ = "0.0.1"


class Registry[ProviderT: BaseProvider, ComponentT: BaseComponent](
    BaseProvider[ProviderT],
    frozen=True,
):
    providers: list[ProviderT]
    _resolved_providers: list[ProviderT] = PrivateAttr([])

    async def __aenter__(self) -> Self:
        for provider in self.providers:
            await self._resolve_provider(provider)
        return self

    async def _resolve_provider(
        self,
        provider: ProviderT,
        *,
        _recursion_guard: RecursionGuard | None = None,
    ) -> None:
        _recursion_guard = _recursion_guard or RecursionGuard(
            "Maximum provider resolution recursion exceeded",
            5,
        )
        _recursion_guard.increment()
        await provider.__aenter__()
        if issubclass(provider.provides_type, BaseProvider):
            async for sub_provider in provider.list():
                await self._resolve_provider(
                    sub_provider,
                    _recursion_guard=_recursion_guard.copy(),
                )
        self._resolved_providers.append(provider)

    async def __aexit__(self, *exc_args: Any) -> bool | None:
        for provider in self._resolved_providers:
            await provider.__aexit__(*exc_args)
        return None

    def provides_uri(self, uri: str) -> bool:
        return any(p.uri == uri for p in self._resolved_providers)

    async def list_cursor(
        self,
        cursor: str | None = None,
    ) -> tuple[Iterable[ProviderT], str | None]:
        assert not cursor
        return self._resolved_providers, None

    async def get_component(
        self,
        uri: str,
        context: ValidationContext | None = None,
    ) -> ComponentT:
        # TODO: provider resolution
        # if self.provides_uri(uri):
        #     return await self.get(uri, context)

        for provider in self._resolved_providers:
            if provider.provides_uri(uri):
                return await provider.get(uri, context)

        raise RuntimeError("Provider not registered for Uri", uri)

    # TODO: move all resolution related methods to context
    async def resolve_component(
        self,
        uri: str,
        context: ValidationContext | None = None,
    ) -> ComponentT:
        component_resolution = ComponentResolutionContext(self, context)
        component = await self.get_component(uri, component_resolution.full_context)
        await component_resolution.resolve()
        return component
