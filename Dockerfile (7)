"""
app/core/auth_deps.py — FastAPI dependencies for authentication.

Two dependencies are provided:

  get_current_user_id(credentials, db) -> str
      Returns the authenticated user's UUID as a string.
      Used by user_dashboards router (existing, unchanged).

  get_current_user(credentials, db) -> User
      Returns the full User ORM object.
      Used by the device-scoped dashboards router so it can read
      user.tenant_id without an extra DB round-trip inside every
      service function.

Both raise HTTP 401 if the JWT is missing, invalid, or expired.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models.models import User

_bearer = HTTPBearer(auto_error=False)


def _resolve_user(
    credentials: HTTPAuthorizationCredentials,
    db: Session,
) -> User:
    """
    Shared logic: validate the Bearer token and return the active User row.
    Raises 401 on any failure so callers never see a partial result.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: str = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> str:
    """
    Return the authenticated user's UUID as a plain string.
    Used by: user_dashboards router (existing callers — unchanged).
    """
    return str(_resolve_user(credentials, db).id)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """
    Return the full authenticated User ORM object.
    Used by: device-scoped dashboards router so the service layer can
    compare device.tenant_id == user.tenant_id in a single query.
    """
    return _resolve_user(credentials, db)
