from typing import TYPE_CHECKING, Any

from pydantic import GetCoreSchemaHandler, SerializationInfo, ValidationInfo
from pydantic_core import CoreSchema, core_schema

if TYPE_CHECKING:
    from .component import BaseComponent
    from .provider import ValidationContext
    from .registry import Registry


class NotResolvedError(RuntimeError):
    """Raised when trying to use a ComponentProxy whose component is not resolved."""


class ComponentUriProxy[ComponentT: "BaseComponent"]:
    """
    Proxy object that is created when a field is given as a URI string
    instead of an actual component instance.

    - `uri` holds the original string (for later resolution).
    - `_component` is set later by your own resolution logic.
    """

    __slots__ = ("_component", "_component_type", "uri")

    def __init__(self, uri: str, component_type: type[ComponentT]) -> None:
        self.uri = uri
        self._component_type = component_type
        self._component: ComponentT | None = None

    def component_proxy_resolve(self, component: ComponentT) -> None:
        """Attach the real component to this proxy."""
        if not isinstance(component, self._component_type):
            raise TypeError(
                "Component wrong instance provided",
                self._component_type,
                component.__class__,
            )
        self._component = component

    def __getattr__(self, name: str) -> Any:
        # normal attribute access is delegated to the wrapped component
        if name in self.__slots__:
            raise AttributeError(name)
        component = self._component
        if component is None:
            raise NotResolvedError(
                f"Component for URI {self.uri!r} has not been resolved",
            )
        return getattr(component, name)

    def __repr__(self) -> str:
        if self._component is None:
            return f"{self.__class__.__name__}(uri={self.uri!r})"
        return repr(self._component)

    def __str__(self) -> str:
        if self._component is None:
            return self.uri
        return str(self._component)


class ComponentResolutionContext[RegistryT: "Registry", ComponentT: "BaseComponent"]:
    registry: RegistryT
    context: "ValidationContext"
    resolved: dict[str, ComponentUriProxy[ComponentT]]
    unresolved: dict[str, ComponentUriProxy[ComponentT]]

    def __init__(
        self,
        registry: RegistryT,
        context: "ValidationContext | None" = None,
    ) -> None:
        self.registry = registry
        self.context = context or {}

        resolution_ctx = self.context.get("component_resolution")
        if resolution_ctx:
            if resolution_ctx.registry is not self.registry:
                raise RuntimeError(
                    "Passed `component_resolution` with other Registry instance",
                    repr(self.registry),
                    repr(resolution_ctx.registry),
                )
            self.resolved = resolution_ctx.resolved
            self.unresolved = resolution_ctx.unresolved
        else:
            self.resolved = {}
            self.unresolved = {}

    def create_component_uri_proxy(
        self,
        uri: str,
        component_type: type[ComponentT],
    ) -> ComponentUriProxy:
        if uri in self.resolved:
            # TODO: check component_type,
            # raise error when we're move this to errors module
            return self.resolved[uri]
        if uri not in self.unresolved:
            self.unresolved[uri] = ComponentUriProxy(uri, component_type)
        return self.unresolved[uri]

    async def _resolve_uri(self, uri: str) -> None:
        proxy = self.unresolved[uri]
        component = await self.registry.get_component(
            uri,
            context=self.full_context,
        )
        proxy.component_proxy_resolve(component)
        self.resolved[uri] = proxy
        # .resolve(self.resolved[uri])
        del self.unresolved[uri]

    @property
    def full_context(self) -> "ValidationContext":
        return {"component_resolution": self, **self.context}

    async def resolve(self) -> None:
        recursion_limit = 10
        recursion_count = 0
        while self.unresolved:
            if recursion_count > recursion_limit:
                raise RecursionError("Resolution recursion limit exceeded")
            for uri in self.unresolved.copy():
                await self._resolve_uri(uri)
            recursion_count += 1


class ComponentUri:
    """
    Annotated metadata for Pydantic v2.

    Used like:
    class SomeModel(BaseModel):
        component: Annotated[ComponentModel, ComponentUri()]

    Validation behaviour:

    - If input is a dict/object: validate as `ComponentModel` (normal behaviour).
    - If input is a str: return `ComponentUriProxy` containing that URI to resolve later
    """

    def __get_pydantic_core_schema__(
        self,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        # Base schema for the original type (e.g. ComponentModel)
        base_schema = handler(source_type)

        def create_component_uri_proxy(
            value: str,
            info: ValidationInfo["ValidationContext"],
        ) -> ComponentUriProxy[Any]:
            if info.context and "component_resolution" in info.context:
                if info.context["component_resolution"] is None:
                    # TODO: explicitly do not resolve.
                    # Where we need this except tests??
                    return ComponentUriProxy(value, source_type)
                return info.context["component_resolution"].create_component_uri_proxy(
                    value,
                    source_type,
                )
            raise RuntimeError("ComponentResolutionContext not provided")
            # return ComponentUriProxy(value)
            #

        def serialize(
            value: ComponentUriProxy[Any],
            info: SerializationInfo,
        ) -> str:
            _ = info
            return value.uri

        uri_schema = core_schema.with_info_after_validator_function(
            create_component_uri_proxy,
            core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                serialize,
                info_arg=True,  # pass SerializationInfo
                return_schema=core_schema.any_schema(),  # result can be str or dict
            ),
        )

        return core_schema.union_schema(
            [uri_schema, base_schema],
        )
