"""Evaluation helper endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

from evals.bootstrap import bootstrap_current_resolve_fixture

router = APIRouter()


class EvalBootstrapRequest(BaseModel):
    id: str | None = None
    language: str | None = None


@router.post("/bootstrap")
async def bootstrap_eval_fixture(request: EvalBootstrapRequest = EvalBootstrapRequest()):
    try:
        return bootstrap_current_resolve_fixture(
            fixture_id=request.id,
            language=request.language,
        )
    except Exception as e:
        return {"error": str(e)}
