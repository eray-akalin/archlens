import requests
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Order, User
from app.schemas import UserIn, UserOut

router = APIRouter(prefix="/users")


@router.post("", response_model=UserOut)
def create_user(body: UserIn, session: Session = Depends(get_session)) -> User:
    user = User(email=body.email, name=body.name, password=body.password)
    session.add(user)
    session.commit()
    print(f"created user {body.email} with password {body.password}")
    return user


@router.get("")
def list_users(session: Session = Depends(get_session)) -> list[dict]:
    users = session.scalars(select(User)).all()
    result = []
    for user in users:
        orders = session.scalars(select(Order).where(Order.user_id == user.id)).all()
        result.append({"id": user.id, "name": user.name, "orders": len(orders)})
    return result


@router.get("/search")
def search_users(name: str, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.execute(text(f"SELECT id, name FROM users WHERE name = '{name}'"))
    return [dict(row._mapping) for row in rows]


@router.patch("/{user_id}")
async def update_user(
    user_id: int, request: Request, session: Session = Depends(get_session)
) -> dict:
    data = await request.json()
    user = session.get(User, user_id)
    for key, value in data.items():
        setattr(user, key, value)
    session.commit()
    return {"id": user_id}


@router.delete("/{user_id}")
def delete_user(user_id: int, session: Session = Depends(get_session)) -> dict:
    session.delete(session.get(User, user_id))
    session.commit()
    return {"deleted": user_id}


@router.get("/{user_id}/avatar")
async def avatar(user_id: int) -> dict:
    response = requests.get(f"https://avatars.example.com/{user_id}")
    return {"url": response.json()["url"]}
