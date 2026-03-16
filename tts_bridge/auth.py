from fastapi import Header, HTTPException, status


def validate_bearer_token(authorization: str | None, expected_token: str) -> None:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Authorization format")

    provided = authorization[len(prefix):].strip()
    if provided != expected_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")


async def auth_dependency(authorization: str | None = Header(default=None)) -> str | None:
    return authorization
