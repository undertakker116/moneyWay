class SBPClient:
    async def create_payment(self, *, order_id: str, amount_rub: str) -> dict:
        """Точка подключения будущего СБП-провайдера для создания payment_url."""
        raise NotImplementedError("SBP payment creation is not implemented yet.")
