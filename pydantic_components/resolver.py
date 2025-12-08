from typing import TYPE_CHECKING, Any, Self

from pydantic import GetCoreSchemaHandler, SerializationInfo, ValidationInfo
from pydantic_core import CoreSchema, core_schema

from .exceptions import (
    ComponentRuntimeError,
    ComponentTypeError,
)
from .utils import RecursionGuard

if TYPE_CHECKING:
    from .component import BaseComponent
    from .provider import ValidationContext
    from .registry import ComponentRegistry


class ComponentUriProxy[ComponentT: "BaseComponent"](str):
    """
    Proxy object that is created when a field is given as a URI string
    instead of an actual component instance.

    - `uri` holds the original string (for later resolution).
    - `_component` is set later by your own resolution logic.
    """

    __slots__ = ("_component", "_component_type", "uri")

    def __new__(cls, uri: str, component_type: type[ComponentT]) -> Self:
        obj = super().__new__(cls, uri)
        obj._component_type = component_type
        return obj

    def __init__(self, uri: str, component_type: type[ComponentT]) -> None:
        self.uri = uri
        self._component_type = component_type
        self._component: ComponentT | None = None

    def _resolve(self, component: ComponentT) -> None:
        """Attach the real component to this proxy."""
        if not isinstance(component, self._component_type):
            raise ComponentTypeError(
                self.uri,
                "Component resolved type and expected type mismatch",
                type(component),
                self._component_type,
            )
        self._component = component

    def __getattr__(self, name: str) -> Any:
        # normal attribute access is delegated to the wrapped component
        if name in self.__slots__:
            raise AttributeError(name)
        component = self._component
        if component is None:
            raise ComponentRuntimeError(
                "Component used before resolved",
                self.uri,
                self._component_type,
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
    def _resolved_with_parents(self) -> dict[str, ComponentT]:
        return {
            **(
                self._parent_context._resolved_with_parents  # noqa:SLF001
                if self._parent_context
                else {}
            ),
            **self._resolved,
        }

    def _get_or_create_proxy(
        self,
        uri: str,
        component_type: type[ComponentT],
    ) -> ComponentUriProxy[ComponentT] | ComponentT:
        component = self._resolved_with_parents.get(uri)
        if component:
            if not issubclass(type(component), component_type):
                raise ComponentTypeError(
                    uri,
                    "Component previously resolved type and expected type mismatch",
                    type(component),
                    component_type,
                )
            return component
        if uri not in self._unresolved:
            self._unresolved[uri] = ComponentUriProxy[ComponentT](uri, component_type)
        return self._unresolved[uri]

    def _load(self, data: object) -> ComponentT:
        component = self._registry.provides_type_adapter.validate_python(
            data,
            context=self._full_validation_context,
        )
        if component.uri in self._resolved_with_parents:
            return self._resolved_with_parents[component.uri]
        self._resolved[component.uri] = component
        return component

    async def _resolve_proxy(self, uri: str) -> None:
        proxy = self._unresolved[uri]
        component = await self._registry.get(
            uri,
            context=self._full_validation_context,
        )
        proxy._resolve(component)  # noqa: SLF001
        self._resolved[uri] = component
        del self._unresolved[uri]

    @property
    def _full_validation_context(self) -> "ValidationContext":
        return {**self._validation_context, "component_context": self}

    async def __aenter__(self) -> Self:
        await self._resolve_all()
        return self

    async def __aexit__(self, *exc_args: object) -> bool | None:
        for component in self._resolved.values():
            aexit = getattr(component, "__aexit__", None)
            if aexit:
                await aexit(*exc_args)

    async def _resolve_all(self) -> None:
        recursion_guard = RecursionGuard("Component resolution recursion exceeded", 10)
        while self._unresolved:
            recursion_guard.increment()
            for uri in self._unresolved.copy():
                await self._resolve_proxy(uri)

    async def resolve(
        self,
        uri_or_data: object,
    ) -> ComponentT:
        if isinstance(uri_or_data, str):
            component = self._resolved_with_parents.get(uri_or_data)
            if component:
                return component
            component = await self._registry.get(
                uri_or_data,
                context=self._full_validation_context,
            )
        else:
            component = self._load(uri_or_data)

        await self._resolve_all()
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
                return info.context["component_context"]._get_or_create_proxy(  # noqa: SLF001
                    value,
                    source_type,
                )
            return ComponentUriProxy[source_type](value, source_type)

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
