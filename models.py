from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.core.config import settings

# bcrypt hard-limits passwords to 72 bytes. passlib 1.7.4 + bcrypt 4.x raises
# ValueError if the input exceeds this. We truncate to 72 bytes before hashing
# and verifying so the app never crashes regardless of password length.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_BCRYPT_MAX = 72


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password[:_BCRYPT_MAX], hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password[:_BCRYPT_MAX])


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
