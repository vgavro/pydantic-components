# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_common.py::test_registry

# Lint
uv run ruff check .

# Type check
uv run mypy pydantic_components/
# and
uv run pyright
```

## Architecture

`pydantic-components` is a library for managing typed, URI-addressed components with lazy resolution and cross-component dependency support via Pydantic v2.

### Core concepts

**`BaseComponent`** (`component.py`) — the base frozen Pydantic model all components extend. Each subclass must implement a `uri: str` property that uniquely identifies the instance.

**`ComponentUri`** (`resolver.py`) — a Pydantic v2 annotation (`Annotated[SomeComponent, ComponentUri()]`) that makes a field accept either a full component dict/object or a URI string. When given a string, validation produces a `ComponentUriProxy` instead of a real component. The proxy acts like the real component via `__getattr__` delegation, but raises `ComponentRuntimeError` if accessed before resolution.

**`ComponentUriProxy`** (`resolver.py`) — a `str` subclass that wraps an unresolved URI. It holds `uri`, `_component_type`, and `_component`. Subclassing `str` was required to avoid Pydantic re-validation issues. Calling `_resolve(component)` attaches the real object.

**`ComponentContext`** (`resolver.py`) — tracks resolved and unresolved components for a registry. Holds `_resolved: dict[str, ComponentT]` and `_unresolved: dict[str, ComponentUriProxy]`. When entered as an async context manager, it calls `_resolve_all()` which iterates until all proxies are resolved (supports circular dependencies up to a recursion limit of 10).

**`BaseProvider`** (`provider.py`) — a `BaseComponent` that produces other components. Exposes `get(uri)`, `list()`, and `list_cursored()` async methods. The generic type parameter `ComponentT` is captured at subclass definition time via `__pydantic_init_subclass__` and stored as `provides_type` class var.

**`RegistryProvider`** (`provider.py`) — concrete provider backed by an in-memory `components` list. Re-validates each component with the active `ValidationContext` on retrieval so that `ComponentUri` fields in the component get the `component_context` injected and produce proper proxies.

**`ComponentRegistry`** (`registry.py`) — top-level entry point, itself a `BaseProvider`. Holds `components` (can be raw component instances or `BaseProvider` instances) and `_providers` (populated on `__aenter__`). Maintains a single `root_context: ComponentContext`. On enter it loads all components, resolves all pending proxies, then enters each provider recursively (providers that provide providers are also recursed, guarded by `RecursionGuard` with a limit of 5).

### Data flow

1. Instantiate `ComponentRegistry[UnionOfTypes](components=[...])`.
2. Use `async with registry:` — this loads direct components into `root_context`, resolves all their URI dependencies, then enters each provider and recursively resolves sub-providers.
3. Retrieve components via `await registry.root_context.resolve(uri_or_data)` or `await registry.get(uri)`.

### `ValidationContext`

Pydantic validation context (`info.context`) carries a `component_context` key of type `ComponentContext`. `ComponentUri` validators read this to register proxies in the active context. `RegistryProvider.get()` and `list()` pass this context so re-validation of retrieved components hooks into the same context.

### Ruff config

`ruff` is configured with `select = ["ALL"]` plus a set of explicit ignores (see `pyproject.toml`). Docstrings (`D`), TODOs (`TD`, `FIX`), `assert` (`S101`), `ANN401`, and `ERA001` are suppressed. The `TRY003`/`EM10x` rules are off so bare string literals in exceptions are fine.
