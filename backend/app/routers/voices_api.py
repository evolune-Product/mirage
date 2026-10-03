from fastapi import APIRouter, Depends

from .. import languages
from ..auth import current_account
from ..db import Account

router = APIRouter()


@router.get("/voices")
def voices(language: str | None = None, acc: Account = Depends(current_account)):
    cat = languages.catalogue()
    if language:
        try:
            code = languages.normalize_language(language)
        except ValueError:
            code = language
        cat["voices"] = [v for v in cat["voices"] if v["language"] == code]
    return cat


@router.get("/languages")
def list_languages(acc: Account = Depends(current_account)):
    return languages.catalogue()["languages"]
