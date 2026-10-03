from fastapi import APIRouter

from .. import signup_guard

router = APIRouter()


@router.get("/signup/challenge")
def signup_challenge():
    """bits=0 means proof-of-work is not required on this deployment."""
    if signup_guard.pow_bits() == 0:
        return {"bits": 0}
    return signup_guard.new_challenge()
