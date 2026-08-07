"""Auth request/response schemas."""
from pydantic import BaseModel

# ponytail: plain str, not EmailStr — skips the email-validator dep. Add it only
# if strict boundary validation of email format becomes a real requirement.


class LoginIn(BaseModel):
    company: str  # subdomain — scopes the user lookup (keeps RLS clean at login)
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class BootstrapIn(BaseModel):
    company_name: str
    subdomain: str
    email: str
    password: str


class MeOut(BaseModel):
    user_id: str
    company_id: str
    role: str
    permissions: list[str]
    # Identity for the UI to greet by. Read from the DB rather than the JWT so
    # a rename takes effect on next page load, not next token refresh.
    email: str | None = None
    full_name: str | None = None
