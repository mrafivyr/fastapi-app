from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import EmailStr


class UserCreate(BaseModel):
    name: str

    email: EmailStr


class UserUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None


class UserResponse(BaseModel):
    id: int

    name: str

    email: EmailStr

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )
