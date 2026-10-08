import json, enum
from collections.abc import *
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any

def dump(data: Any, /, short: bool = False) -> dict[str, Any]:
    '''TODO: doc'''
    keys: tuple | None = getattr(data, '__ser_keys__', lambda _: None)(short)
    if keys is not None:
        data = {k: getattr(data, k, None) for k in keys}
    elif isinstance(data, BaseModel):
        data  = data.dict()
    elif isinstance(data, enum.Enum):
        data = data.name
    if not isinstance(data, Sequence | Mapping | set):
        if not isinstance(data, int | float | bool | str | None):
            data = str(data)
        return data
    stack: list[tuple[list | dict, Iterator[tuple[Any, Any]]]] = []
    stack_mut: list[int] = []
    if isinstance(data, Mapping):
        data = {str(k): v for k, v in data.items()}
        stack.append((data.copy(), iter(data.items())))
    else:
        data = list(data)
        stack.append((data.copy(), enumerate(data)))
    stack_mut.append(len(data))
    while len(stack):
        idx, val = next(stack[-1][1])
        stack_mut[-1] -= 1
        keys: tuple | None = getattr(val, '__ser_keys__', lambda short=False: None)(short)
        if keys is not None:
            val = {k: getattr(val, k) for k in keys}
        if isinstance(val, Mapping):
            d = {str(k): v for k, v in val.items()}
            stack[-1][0][idx] = d
            stack.append((d, iter(d.items())))
            stack_mut.append(len(d))
            continue
        elif isinstance(val, Sequence | set):
            l = list(val)
            stack[-1][0][idx] = l
            stack.append((l, enumerate(l)))
            stack_mut.append(len(l))
            continue
        elif not isinstance(val, int | float | str | bool | None):
            stack[-1][0][idx] = str(val)
        while len(stack_mut) and stack_mut[-1] == 0:
            data = stack.pop()[0]
            stack_mut.pop()
    return data

def dumps(data: Any, /, short: bool = False, **kw) -> str:
    '''TODO: doc'''
    kw['ensure_ascii'] = kw.get('ensure_ascii', False)
    return json.dumps(dump(data, short), **kw)

def make_response(*, content: Any, short: bool = False, **kw) -> JSONResponse:
    '''TODO: doc'''
    return JSONResponse(content=dump(content, short), **kw)

load = json.load
loads = json.loads