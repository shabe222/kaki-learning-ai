from pydantic import BaseModel, Field
from typing import Optional


class RegisterRequest(BaseModel):
    username: str
    password: str = Field(min_length=6, max_length=128)
    email: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class UserProfileResponse(BaseModel):
    user_id: int
    username: str
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    has_profile: bool = False
