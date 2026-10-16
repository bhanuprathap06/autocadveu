"""Engineering calculator endpoints."""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends

from ..calc.calculators import CALCULATORS, SAFETY_NOTE, CalcError, run
from ..core.errors import Unprocessable
from ..db import models as M
from .deps import current_user

router = APIRouter(tags=["calculators"])


@router.get("/calculators")
def list_calculators(user: M.User = Depends(current_user)) -> dict[str, Any]:
    items = []
    for k, c in CALCULATORS.items():
        sig = inspect.signature(c["fn"])
        params = [{"name": n, "required": p.default is inspect.Parameter.empty,
                   "default": None if p.default is inspect.Parameter.empty else p.default} for n, p in sig.parameters.items()]
        items.append({"key": k, "name": c["name"], "domain": c["domain"], "parameters": params, "doc": (c["fn"].__doc__ or "").strip()})
    return {"items": items, "note": SAFETY_NOTE}


@router.post("/calculators/{key}")
def calculate(key: str, inputs: dict[str, Any], user: M.User = Depends(current_user)) -> dict[str, Any]:
    try:
        return run(key, inputs)
    except CalcError as exc:
        raise Unprocessable(str(exc), code="invalid_input") from exc
