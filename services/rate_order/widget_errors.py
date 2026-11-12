class WidgetDeliveryError(RuntimeError):
    def __init__(self, kind: str, *, retry_after: float = 0) -> None:
        super().__init__(kind)
        self.kind = kind
        self.retry_after = retry_after
