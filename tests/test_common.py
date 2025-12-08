from typing import Annotated

import pytest
from pydantic import Field

from pydantic_components.component import BaseComponent
from pydantic_components.exceptions import (
    ComponentNotFoundError,
    ComponentRuntimeError,
    ComponentTypeError,
)
from pydantic_components.provider import RegistryProvider
from pydantic_components.registry import (
    ComponentRegistry,
)
from pydantic_components.resolver import (
    ComponentUri,
    ComponentUriProxy,
)


class TestComponent(BaseComponent, frozen=True):
    @property
    def uri(self) -> str:
        return f"uri://{self.value}"

    value: str


class TestDepComponent(BaseComponent, frozen=True, revalidate_instances="always"):
    # NOTE: trick with re-validation is needed to create components outside
    # registry validation_context
    # model_config: ClassVar[ConfigDict] = {"revalidate_instances": "always"}

    @property
    def uri(self) -> str:
        return f"uri://test_dep/{self.test.uri.split('//')[-1]}"

    test: Annotated[TestComponent, ComponentUri()]


def test_component_uri_with_data() -> None:
    # dict/object -> normal model
    test_dep = TestDepComponent.model_validate({"test": {"value": "test1"}})
    assert isinstance(test_dep.test, TestComponent)
    assert test_dep.test.value == "test1"

    # serializes to uri
    assert test_dep.model_dump()["test"] == "uri://test1"


def test_component_uri_with_uri() -> None:
    test_dep = TestDepComponent.model_validate(
        {"test": "uri://test1"},
    )
    assert isinstance(test_dep.test, ComponentUriProxy)
    assert test_dep.test.uri == "uri://test1"
    assert test_dep.test._component_type is TestComponent  # noqa: SLF001

    # Using it before resolution raises
    with pytest.raises(ComponentRuntimeError):
        _ = test_dep.test.value

    # Later, your own code can resolve it
    test_dep.test._resolve(TestComponent(value="test1"))  # noqa: SLF001
    assert test_dep.test.value == "test1"  # works via proxy

    # serializes to uri
    assert test_dep.model_dump()["test"] == "uri://test1"


async def test_registry() -> None:
    registry = ComponentRegistry[
        RegistryProvider[TestComponent]
        | RegistryProvider[TestDepComponent]
        | TestComponent
        | TestDepComponent
    ](
        components=[
            RegistryProvider[TestComponent](
                components=[TestComponent.model_validate({"value": "test1"})],
            ),
            RegistryProvider[TestDepComponent](
                components=[
                    TestDepComponent.model_validate({"test": "uri://test1"}),
                ],
            ),
        ],
    )
    await registry.__aenter__()
    test_dep = await registry.root_context.resolve("uri://test_dep/test1")
    assert isinstance(test_dep, TestDepComponent)
    assert test_dep.test.uri == "uri://test1"
    assert test_dep.test.value == "test1"


async def test_registry_circular() -> None:
    class TestComponentCircular(TestComponent, frozen=True):
        test_dep: Annotated[TestDepComponent, ComponentUri()]

    registry = ComponentRegistry[
        RegistryProvider[TestComponentCircular]
        | RegistryProvider[TestDepComponent]
        | TestComponent
        | TestDepComponent
    ](
        components=[
            RegistryProvider[TestComponentCircular](
                components=[
                    TestComponentCircular.model_validate(
                        {"value": "test1", "test_dep": "uri://test_dep/test1"},
                    ),
                ],
            ),
            RegistryProvider[TestDepComponent](
                components=[
                    TestDepComponent.model_validate({"test": "uri://test1"}),
                ],
            ),
        ],
    )
    await registry.__aenter__()
    test_dep = await registry.root_context.resolve("uri://test_dep/test1")
    assert isinstance(test_dep, TestDepComponent)
    assert test_dep.test.uri == "uri://test1"
    assert test_dep.test.value == "test1"

    test1 = await registry.root_context.resolve("uri://test1")
    assert isinstance(test1, TestComponentCircular)
    assert test1.test_dep.uri == "uri://test_dep/test1"


async def test_registry_errors() -> None:
    registry = ComponentRegistry[
        RegistryProvider[TestComponent]
        | RegistryProvider[TestDepComponent]
        | TestComponent
        | TestDepComponent
    ](
        components=[
            RegistryProvider[TestComponent](
                components=[
                    TestComponent(value="test1"),
                ],
            ),
            RegistryProvider[TestDepComponent](
                components=[
                    # "uri://test_dep/test1"
                    TestDepComponent.model_validate({"test": "uri://test1"}),
                    # "uri://test_dep/test_dep/test1"
                    TestDepComponent.model_validate({"test": "uri://test_dep/test1"}),
                ],
            ),
        ],
    )
    await registry.__aenter__()
    await registry.root_context.resolve("uri://test_dep/test1")
    with pytest.raises(ComponentNotFoundError):
        await registry.root_context.resolve("uri://test_dep/unknown")
    with pytest.raises(ValueError, match="No providers found"):
        await registry.root_context.resolve("uri://test_dep/unknown")
    with pytest.raises(ComponentTypeError):
        await registry.root_context.resolve("uri://test_dep/test_dep/test1")
    with pytest.raises(TypeError):
        await registry.root_context.resolve("uri://test_dep/test_dep/test1")


async def test_dep_exclude_default() -> None:
    class TestDepExcludeDefaultComponent(TestDepComponent, frozen=True):
        test: Annotated[TestComponent, ComponentUri()] = Field(
            default="uri://test1",
            exclude=True,
            validate_default=True,
        )

    registry = ComponentRegistry[TestComponent | TestDepExcludeDefaultComponent](
        components=[
            TestComponent(value="test1"),
            TestDepExcludeDefaultComponent(),
        ],
    )
    await registry.__aenter__()
    test_dep = await registry.root_context.resolve("uri://test_dep/test1")
    assert isinstance(test_dep, TestDepExcludeDefaultComponent)
    assert test_dep.test.value == "test1"
