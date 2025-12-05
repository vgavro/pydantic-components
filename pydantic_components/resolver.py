from typing import TYPE_CHECKING, Any, Self

from pydantic import GetCoreSchemaHandler, SerializationInfo, ValidationInfo
from pydantic_core import CoreSchema, core_schema

from .utils import RecursionGuard

if TYPE_CHECKING:
    from .component import BaseComponent
    from .provider import ValidationContext
    from .registry import ComponentRegistry


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

    def _resolve(self, component: ComponentT) -> None:
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


class ComponentContext[RegistryT: "ComponentRegistry", ComponentT: "BaseComponent"]:
    _registry: RegistryT
    _validation_context: "ValidationContext"
    _parent_context: Self | None
    _resolved: dict[str, ComponentT]
    _unresolved: dict[str, ComponentUriProxy[ComponentT]]

    def __init__(
        self,
        registry: RegistryT,
        parent_context: Self | None = None,
        validation_context: "ValidationContext | None" = None,
    ) -> None:
        self._registry = registry
        if parent_context and parent_context._registry is not self._registry:  # noqa:SLF001
            raise ValueError(
                "parent_context registry and provided registry are not the same",
            )
        self._parent_context = parent_context
        self._validation_context = validation_context or {}
        self._resolved = {}
        self._unresolved = {}

    @property
    def _all_resolved(self) -> dict[str, ComponentT]:
        return {
            **(self._parent_context._all_resolved if self._parent_context else {}),  # noqa:SLF001
            **self._resolved,
        }

    def _get_or_create_proxy(
        self,
        uri: str,
        component_type: type[ComponentT],
    ) -> ComponentUriProxy[ComponentT] | ComponentT:
        if uri in self._all_resolved:
            # TODO: check component_type,
            # raise error when we're move this to errors module
            return self._all_resolved[uri]
        if uri not in self._unresolved:
            self._unresolved[uri] = ComponentUriProxy[ComponentT](uri, component_type)
        return self._unresolved[uri]

    def _load_component(self, data: object) -> ComponentT:
        component = self._registry.provides_type_adapter.validate_python(
            data,
            context=self.full_validation_context,
        )
        if component.uri in self._all_resolved:
            return self._all_resolved[component.uri]
        self._resolved[component.uri] = component
        return component

    async def _resolve_uri(self, uri: str) -> None:
        proxy = self._unresolved[uri]
        component = await self._registry.get(
            uri,
            context=self.full_validation_context,
        )
        proxy._resolve(component)  # noqa: SLF001
        self._resolved[uri] = component
        del self._unresolved[uri]

    @property
    def full_validation_context(self) -> "ValidationContext":
        return {**self._validation_context, "component_context": self}

    async def __aenter__(self) -> Self:
        await self._resolve()
        return self

    async def __aexit__(self, *exc_args: object) -> bool | None:
        for component in self._resolved.values():
            aexit = getattr(component, "__aexit__", None)
            if aexit:
                await aexit(*exc_args)

    async def _resolve(self) -> None:
        recursion_guard = RecursionGuard("Component resolution recursion exceeded", 10)
        while self._unresolved:
            recursion_guard.increment()
            for uri in self._unresolved.copy():
                await self._resolve_uri(uri)

    async def resolve(
        self,
        uri_or_data: object,
    ) -> ComponentT:
        if isinstance(uri_or_data, str):
            if uri_or_data in self._all_resolved:
                return self._all_resolved[uri_or_data]

            component = await self._registry.get(
                uri_or_data,
                context=self.full_validation_context,
            )
        else:
            component = self._load_component(uri_or_data)

        await self._resolve()
        return component

    def create_context(
        self,
        validation_context: "ValidationContext | None" = None,
    ) -> Self:
        return self.__class__(
            registry=self._registry,
            parent_context=self,
            validation_context={
                **self._validation_context,
                **(validation_context or {}),
            },
        )


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
            if info.context and "component_context" in info.context:
                if info.context["component_context"] is None:
                    # TODO: explicitly do not resolve.
                    # Where we need this except tests??
                    return ComponentUriProxy[source_type](value, source_type)
                return info.context["component_context"]._get_or_create_proxy(  # noqa: SLF001
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
