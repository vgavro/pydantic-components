class RecursionGuard:
    def __init__(self, message: str, limit: int, current: int = -1) -> None:
        self.message = message
        self.limit = limit
        self.current = current

    def increment(self) -> None:
        self.current += 1
        if self.current > self.limit:
            raise RecursionError(self.message, self.limit)

    def copy(self) -> "RecursionGuard":
        return RecursionGuard(self.message, self.limit, self.current)
