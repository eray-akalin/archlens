from app.services.notify import send_receipt


def charge(user_id: int, cents: int) -> None:
    send_receipt(user_id, cents)


def format_amount(cents: int) -> str:
    return f"${cents / 100:.2f}"
