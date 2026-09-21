from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from observability import MetricsSnapshot


class UserRole(StrEnum):
    cashier = "cashier"
    manager = "manager"
    accountant = "accountant"
    owner = "owner"
    admin = "admin"


class ApiUser(BaseModel):
    id: int
    tg_user_id: int
    display_name: str
    role: UserRole
    cities: list[str] = Field(default_factory=list)
    is_active: bool


class HealthResponse(BaseModel):
    status: str


class DurationMetricResponse(BaseModel):
    count: int
    successes: int
    errors: int
    cancellations: int
    total_seconds: float
    max_seconds: float
    last_seconds: float


class MetricsResponse(BaseModel):
    use_cases: dict[str, DurationMetricResponse]
    queues: dict[str, int]

    @classmethod
    def from_snapshot(cls, snapshot: MetricsSnapshot) -> MetricsResponse:
        return cls(
            use_cases={
                name: DurationMetricResponse.model_validate(metric, from_attributes=True)
                for name, metric in snapshot.use_cases.items()
            },
            queues=dict(snapshot.queues),
        )


class TelegramOidcLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    code_verifier: str = Field(min_length=43, max_length=128)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    nonce: str = Field(min_length=16, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: ApiUser
