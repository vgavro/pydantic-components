from typing import Annotated, Any, cast

import pytest
from pydantic import BaseModel, Field

from pydantic_components.registry import (
    BaseComponent,
    BaseProvider,
    ComponentRegistry,
)
from pydantic_components.resolver import (
    ComponentUri,
    ComponentUriProxy,
    NotResolvedError,
)

# TODO: there is too much copy-paste code in tests,
# refactor to illustrate usage in more elegant way


def test_component_uri() -> None:
    class ComponentModel(BaseModel):
        x: int
        y: int

    class SomeModel(BaseModel):
        component: Annotated[ComponentModel, ComponentUri()]

    # dict/object -> normal model
    m1 = SomeModel.model_validate({"component": {"x": 1, "y": 2}})
    assert isinstance(m1.component, ComponentModel)
    assert m1.component.x == 1

    # string -> proxy
    m2 = SomeModel.model_validate(
        {"component": "component://foo"},
        context={"component_context": None},
    )
    assert isinstance(m2.component, ComponentUriProxy)
    assert m2.component.uri == "component://foo"

    # Using it before resolution raises
    with pytest.raises(NotResolvedError):
        _ = m2.component.x

    # Later, your own code can resolve it
    m2.component._resolve(ComponentModel(x=1, y=2))  # noqa: SLF001
    assert m2.component.x == 1  # works via proxy


def test_component_serialize() -> None:
    class ComponentModel(BaseModel):
        x: int
        y: int

    class SomeModel(BaseModel):
        component: Annotated[ComponentModel, ComponentUri()]

    # dict/object -> normal model
    m1 = SomeModel.model_validate({"component": {"x": 1, "y": 2}})
    assert isinstance(m1.component, ComponentModel)
    assert m1.component.x == 1

    # string -> proxy
    m2 = SomeModel.model_validate(
        {"component": "component://foo"},
        context={"component_context": None},
    )
    assert isinstance(m2.component, ComponentUriProxy)
    assert m2.component.uri == "component://foo"

    # Using it before resolution raises
    with pytest.raises(NotResolvedError):
        _ = m2.component.x

    # Later, your own code can resolve it
    m2.component._resolve(ComponentModel(x=1, y=2))  # noqa: SLF001
    assert m2.component.x == 1  # works via proxy


async def test_registry() -> None:
    class Test1Component(BaseComponent, frozen=True):
        @property
        def uri(self) -> str:
            return "test1/1"

        test1_value: int = 1
        test2_component: Annotated["Test2Component", ComponentUri()]

    class Test1Provider(BaseProvider[Test1Component], frozen=True):
        def provides_uri(self, uri: str) -> bool:
            return uri.startswith("test1/")

        async def get(self, uri: str, context: Any = None) -> Test1Component:
            _, _ = uri, context
            return Test1Component.model_validate(
                {"test2_component": "test2/1"},
                context=context,
            )

    class Test2Component(BaseComponent, frozen=True):
        @property
        def uri(self) -> str:
            return "test2/1"

        test2_value: int = 1

        test1_component: Annotated[Test1Component, ComponentUri()]

    # needed just for testing cyclic dependencies
    Test1Component.model_rebuild()

    class Test2Provider(BaseProvider[Test2Component], frozen=True):
        def provides_uri(self, uri: str) -> bool:
            return uri.startswith("test2/")

        async def get(self, uri: str, context: Any | None = None) -> Test2Component:
            _ = uri
            return Test2Component.model_validate(
                {"test1_component": "test1/1"},
                context=context,
            )

    registry = ComponentRegistry[Test1Provider | Test2Provider](
        components=[Test1Provider(), Test2Provider()],
        validation_context={},
    )
    await registry.__aenter__()
    test2_component = cast(
        "Test2Component",
        await registry.root_context.resolve("test2/1"),
    )
    assert test2_component.test1_component.uri == "test1/1"
    assert test2_component.test1_component.test1_value == 1
    assert test2_component.test1_component.test2_component.uri == "test2/1"
    assert test2_component.test1_component.test2_component.test2_value == 1


async def test_wrong_type_resolved() -> None:
    class Test1Component(BaseComponent, frozen=True):
        @property
        def uri(self) -> str:
            return "test1/1"

        test1_value: int = 1
        test2_component: Annotated["Test2Component", ComponentUri()]

    class Test1Provider(BaseProvider[Test1Component], frozen=True):
        def provides_uri(self, uri: str) -> bool:
            return uri.startswith("test1/")

        async def get(self, uri: str, context: Any = None) -> Test1Component:
            _, _ = uri, context
            return Test1Component.model_validate(
                {"test2_component": "test2/1"},
                context=context,
            )

    class Test2Component(BaseComponent, frozen=True):
        @property
        def uri(self) -> str:
            return "test2/1"

        test2_value: int = 1

        test1_component: Annotated["Test2Component", ComponentUri()]

    # needed just for testing cyclic dependencies
    Test1Component.model_rebuild()

    class Test2Provider(BaseProvider[Test2Component], frozen=True):
        def provides_uri(self, uri: str) -> bool:
            return uri.startswith("test2/")

        async def get(self, uri: str, context: Any | None = None) -> Test2Component:
            _ = uri
            return Test2Component.model_validate(
                {"test1_component": "test1/1"},
                context=context,
            )

    registry = ComponentRegistry[
        Test1Provider | Test2Provider | Test1Component | Test2Component
    ](
        components=[Test1Provider(), Test2Provider()],
        validation_context={},
    )
    await registry.__aenter__()
    with pytest.raises(TypeError):
        await registry.root_context.resolve("test2/1")


async def test_deps_excluded() -> None:
    class Test1Component(BaseComponent, frozen=True):
        @property
        def uri(self) -> str:
            return "test1/1"

        test1_value: int = 1

        test2_component: Annotated["Test2Component", ComponentUri()] = Field(
            "test2/1",
            exclude=True,
            validate_default=True,
        )

    class Test1Provider(BaseProvider[Test1Component], frozen=True):
        def provides_uri(self, uri: str) -> bool:
            return uri.startswith("test1/")

        async def get(self, uri: str, context: Any = None) -> Test1Component:
            _, _ = uri, context
            return Test1Component.model_validate(
                {},
                # {"test2_component": "test2/1"},
                context=context,
            )

    class Test2Component(BaseComponent, frozen=True):
        @property
        def uri(self) -> str:
            return "test2/1"

        test2_value: int = 1

        test1_component: Annotated["Test1Component", ComponentUri()]

    # needed just for testing cyclic dependencies
    Test1Component.model_rebuild()

    class Test2Provider(BaseProvider[Test2Component], frozen=True):
        def provides_uri(self, uri: str) -> bool:
            return uri.startswith("test2/")

        async def get(self, uri: str, context: Any | None = None) -> Test2Component:
            _ = uri
            return Test2Component.model_validate(
                {"test1_component": "test1/1"},
                context=context,
            )

    registry = ComponentRegistry[
        Test1Provider | Test2Provider | Test1Component | Test2Component
    ](
        components=[Test1Provider(), Test2Provider()],
        validation_context={},
    )
    await registry.__aenter__()
    test1 = cast("Test1Component", await registry.root_context.resolve("test1/1"))
    assert test1.test2_component.uri == "test2/1"
    assert test1.test2_component.test2_value == 1
