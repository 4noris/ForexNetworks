"""Keep large read-response formatting off the live ASGI event loop."""
import functools
import inspect

import orjson
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from .live_stream import finite_float, prepare_live_json
from .websocket_reads import READ_POST_PATHS


class LiveJSONResponse(JSONResponse):
    def render(self, content):
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS)


class LiveReadRoute(APIRoute):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        eligible = (self.path in {"/api/manager/accounts", "/api/manager/dashboard"}
                    or self.path in READ_POST_PATHS and "POST" in self.methods)
        endpoint = self.dependant.call
        # Keep response-model validation/filtering and asynchronous endpoints
        # on their original FastAPI path. Never wrap settings or trade writes.
        if not eligible or self.response_model is not None or inspect.iscoroutinefunction(endpoint):
            return

        @functools.wraps(endpoint)
        def encoded_read(*values, **params):
            data = endpoint(*values, **params)
            if isinstance(data, Response):
                return data
            encoded = prepare_live_json(data) if self.path == "/api/risk/live" else jsonable_encoder(data, custom_encoder={float: finite_float})
            temporary = next((value for value in params.values() if isinstance(value, Response)), None)
            status = (temporary.status_code if temporary is not None else None) or self.status_code or 200
            result = LiveJSONResponse(encoded, status_code=status)
            if temporary is not None:
                result.raw_headers.extend((key, value) for key, value in temporary.raw_headers
                                          if key not in {b"content-length", b"content-type"})
            return result

        self.dependant.call = encoded_read
