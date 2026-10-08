from typing import (
    Annotated,
    Any,
    Literal,
)

from fastapi import (
    Body,
    APIRouter,
    Header,
    HTTPException,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
)

from urllib.request import (
    HTTPError,
    Request,
    URLError,
    urlopen,
)

from urllib.parse import (
    urlsplit,
    parse_qs,
)

from socket import (
    herror,
    gaierror,
)

from datetime import datetime
from hashlib import md5
from uuid import uuid4

import ssl, json

# XH signature key, dumped from xhcore.so
KEY = 'eptim]q34imt5b]-q04i5q=fdkfjfsadlkjfasdfrt573df4pltoy]-pn965498d' #64 bits

def md5hash(s):
    return md5(s.encode('utf-8')).hexdigest()

def sign_request(
    version: str,
    method: str,
    url: str,
    body: str | None,
    token: str,
    headers: dict[str, Any], # dict is mutated
):
    time = int(datetime.now().timestamp() * 1000)
    match version:
        case 'disabled':
            return url
        case 'V1':
            raw = f'{method}{url}{time}{KEY}{body or ''}Authorization: {token}'
            sign = md5hash(raw)
            if '?' in url: return f'{url}&sign={sign}&t={time}'
            else: return f'{url}?sign={sign}&t={time}'
        case 'encrytred':
            # https://cloud-resource.yunzuoye.net/js/app.js (@/utils/axios.ts)
            uuid = str(uuid4()).replace('-','')
            appCode = 'WEB002001'
            obj = {
                'appCode': appCode,
                'appKey': md5hash('XH' + appCode),
                'timestamp': time,
            }
            if method == 'GET':
                segments = urlsplit(url)
                qs = parse_qs(segments.query, keep_blank_values=True)
                obj |= qs
            raw = ''.join(f'{key}={obj[key]}&' for key in sorted(obj.keys()))
            sign = md5hash(md5hash(raw + (body or '')))
            print(uuid)
            headers.update({
                'timestamp': str(time),
                'sign': sign,
                'X-B3-SpanId': uuid[0:16],
                'X-B3-TraceId': uuid[-30:],
                'loginSign': '', # 这个字段出现在XH代码里，但是似乎没有用
                'token': token,
            })
            return url
        case _:
            raise HTTPException(status_code=503, detail="暂不支持")

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.VerifyMode.CERT_NONE

app = APIRouter()

class XHRequestOptions(BaseModel):
    sign_version: str = 'V1'
    response_is_json: bool = True
    response_encoding: str = 'utf-8'

class XHBaseRequest(BaseModel):
    method: Literal['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
    url: str
    headers: dict[str, Any]
    body: dict[str, Any] | None
    token: str = ''
    options: XHRequestOptions = XHRequestOptions()

class XHBodylessRequest(XHBaseRequest):
    method: Literal['GET', 'DELETE', 'HEAD', 'OPTIONS']
    body: None

class XHBodyRequest(XHBaseRequest):
    method: Literal['POST', 'PUT', 'PATCH']
    body: dict[str, Any] | list[dict[str, Any]]

type XHRequest = Annotated[
    XHBodylessRequest | XHBodyRequest,
    Field(discriminator='method'),
]

@app.post('/api')
def xh_api(
    data: XHRequest = Body(...),
    user_agent: str = Header('Mozilla/5.0'),
):
    try:
        raw_response = data.options.response_encoding == 'raw'
        if not raw_response:
            b''.decode(data.options.response_encoding) # early errors
        # fixed: set the default ua to browser ua instead of `python-urllib/x.x.x`
        headers = {'User-Agent': user_agent, 'Content-Type': 'application/json'} | data.headers | {'Authorization': data.token}
        body = None if data.body is None else json.dumps(data.body, ensure_ascii=False)
        body_encoded = None if body is None else body.encode('utf-8')
        if not data.token:
            del headers['Authorization']
        signed_url = sign_request(
            data.options.sign_version,
            data.method,
            data.url.split('#')[0],
            None if body is None or len(body_encoded) >= 1048576 else body,
            data.token,
            headers,
        )
        # TODO rewrite this using the `requests` module instead
        req = Request(
            signed_url,
            data=body_encoded,
            headers=headers,
            method=data.method,
        )
        try:
            resp = urlopen(req, timeout=10, context=ssl_ctx)
        except HTTPError as error_resp:
            resp = error_resp
        except URLError as error:
            if isinstance(error.reason, BaseException): raise error.reason from None
            raise
        resp_head = dict(resp.getheaders())
        resp_data = resp.read() if raw_response else resp.read().decode(data.options.response_encoding)
        resp_body = json.loads(resp_data) if data.options.response_is_json and resp_data and not raw_response else resp_data
        return {
            'actual_url': resp.url,
            'status': resp.status,
            'headers': resp_head,
            'body': resp_body,
        }
    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="接口没有返回JSON数据")
    except (herror, gaierror):
        raise HTTPException(status_code=502, detail="域名解析失败")
    except TimeoutError:
        raise HTTPException(status_code=504, detail="网络请求超时")
    except ConnectionAbortedError:
        raise HTTPException(status_code=502, detail="连接中断")
    except ConnectionRefusedError:
        raise HTTPException(status_code=502, detail="连接被拒绝")
    except ConnectionResetError:
        raise HTTPException(status_code=502, detail="连接已重置")
    except URLError:
        raise HTTPException(status_code=502, detail="接口访问失败")
    except UnicodeEncodeError:
        raise HTTPException(status_code=502, detail="接口没有返回Unicode编码的数据")
    except (IndexError, KeyError):
        raise HTTPException(status_code=500, detail="哎呀，出错了")
    except LookupError:
        raise HTTPException(status_code=400, detail="指定的编码不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail="哎呀，出错了！"+str(e))

#@app.get("/index.html")
#async def index():
#    return FileResponse('./xhapi.html')
