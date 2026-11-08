from typing import NoReturn

from fastapi import HTTPException


def not_implemented() -> NoReturn:
    raise HTTPException(status_code=501, detail="Not implemented.")
