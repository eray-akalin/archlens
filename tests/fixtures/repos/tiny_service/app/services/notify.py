from app.services.billing import format_amount


def send_receipt(user_id: int, cents: int) -> None:
    print(f"receipt for user {user_id}: {format_amount(cents)}")
