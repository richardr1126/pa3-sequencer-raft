"""Financial SOAP server entrypoint."""

import argparse
import logging
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backends.financial.service import (
    InvalidCardError,
    parse_process_transaction_request,
    process_transaction,
    soap_fault_response,
    soap_success_response,
    wsdl_document,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="Financial SOAP Service")

    @app.get("/")
    async def get_root(request: Request) -> Response:
        # SOAP clients fetch service metadata via `?wsdl`; plain `/` is not a valid endpoint.
        if not any(key.lower() == "wsdl" for key in request.query_params.keys()):
            return Response(status_code=404)

        wsdl = wsdl_document(str(request.url.replace(query="")))
        return Response(content=wsdl, media_type="text/xml; charset=utf-8")

    @app.post("/")
    async def soap_endpoint(request: Request) -> Response:
        # Preserve malformed bytes so we can still return a SOAP fault instead of crashing decode.
        raw = (await request.body()).decode("utf-8", errors="replace")
        try:
            payload = parse_process_transaction_request(raw)
            result = process_transaction(**payload)
            body = soap_success_response(result)
            return Response(content=body, media_type="text/xml; charset=utf-8")
        except InvalidCardError as exc:
            body = soap_fault_response(
                fault_code="Client.InvalidCard",
                fault_string=str(exc),
            )
            # SOAP 1.1 faults conventionally use HTTP 500 with a structured XML fault body.
            return Response(content=body, media_type="text/xml; charset=utf-8", status_code=500)
        except ValueError as exc:
            body = soap_fault_response(
                fault_code="Client",
                fault_string=str(exc),
            )
            return Response(content=body, media_type="text/xml; charset=utf-8", status_code=500)
        except Exception:
            body = soap_fault_response(
                fault_code="Server",
                fault_string="Internal server error",
            )
            return Response(content=body, media_type="text/xml; charset=utf-8", status_code=500)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Financial Transactions SOAP Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8005, help="Port to bind to")
    args = parser.parse_args()

    logger.info("Financial SOAP server listening on %s:%s", args.host, args.port)
    logger.info("WSDL URL: http://%s:%s/?wsdl", args.host, args.port)
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
