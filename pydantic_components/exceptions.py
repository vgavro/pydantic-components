class ComponentError(Exception):
    def __init__(self, uri: str, message: str, *args: object) -> None:
        super().__init__(uri, message, *args)
        self.uri = uri
        self.message = message


class ComponentNotFoundError(ComponentError, ValueError):
    """Raised when component URI wasn't found."""


class ComponentTypeError(ComponentError, TypeError):
    """Raised on provided type and requested type mismatch."""

    def __init__(
        self,
        uri: str,
        message: str,
        provided_type: type[object],
        expected_type: type[object],
    ) -> None:
        super().__init__(uri, message, provided_type, expected_type)
        self.provided_type = provided_type
        self.expected_type = expected_type


class ComponentRuntimeError(RuntimeError):
    """Raised on wrong component usage."""
