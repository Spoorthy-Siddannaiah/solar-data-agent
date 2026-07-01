"""Trusted user context construction for the demo service layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import User


class UserContextError(ValueError):
    """Base error for demo user context resolution."""


class UserNotFoundError(UserContextError):
    """Raised when no stored user matches the supplied identifier."""


class AmbiguousUserError(UserContextError):
    """Raised when an identifier is not globally unique in the demo store."""


@dataclass(frozen=True)
class UserContext:
    user_id: str
    company_id: str
    role: str
    can_view_energy: bool
    can_view_financials: bool


def load_user_context(
    session: Session,
    *,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
) -> UserContext:
    """Resolve identity from stored user data without caller-provided tenancy."""
    if (user_id is None) == (email is None):
        raise UserContextError("provide exactly one of user_id or email")

    if user_id is not None:
        identifier = user_id.strip()
        if not identifier:
            raise UserContextError("user_id must not be empty")
        predicate = User.user_id == identifier
    else:
        identifier = email.strip() if email is not None else ""
        if not identifier:
            raise UserContextError("email must not be empty")
        predicate = User.email == identifier

    users = session.scalars(select(User).where(predicate).limit(2)).all()
    if not users:
        raise UserNotFoundError("user not found")
    if len(users) > 1:
        raise AmbiguousUserError("user identifier is ambiguous")

    user = users[0]
    if not user.active:
        raise UserNotFoundError("user not found")
    if user.access_scope == "energy":
        can_view_energy = True
        can_view_financials = False
    elif user.access_scope == "energy+financial":
        can_view_energy = True
        can_view_financials = True
    else:
        raise UserContextError("stored user has an unsupported access scope")

    return UserContext(
        user_id=user.user_id,
        company_id=user.company_id,
        role=user.role,
        can_view_energy=can_view_energy,
        can_view_financials=can_view_financials,
    )
