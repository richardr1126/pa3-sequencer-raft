"""Backend buyers FastAPI server."""

import argparse
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backends.common.errors import ApiError
from backends.common.db_client import CustomerDbClient, ProductDbClient
from backends.common.financial_client import FinancialClient
from backends.buyers.api import BuyerService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


class CreateAccountRequest(BaseModel):
    buyer_name: str
    login_name: str
    password: str


class LoginRequest(BaseModel):
    login_name: str
    password: str


class AddCartItemRequest(BaseModel):
    item_category: int
    item_id: int
    quantity: int = 1


class FeedbackRequest(BaseModel):
    item_category: int
    item_id: int
    vote: str


class MakePurchaseRequest(BaseModel):
    card_holder_name: str
    credit_card_number: str
    expiration_date: str
    security_code: str


def create_app(
    customer_db_host: str,
    customer_db_port: int,
    product_db_host: str,
    product_db_port: int,
    financial_wsdl_url: str,
) -> FastAPI:
    """Create FastAPI app for buyers backend."""
    logger.info(f"  Customer DB: {customer_db_host}:{customer_db_port}")
    logger.info(f"  Product DB: {product_db_host}:{product_db_port}")
    logger.info("  Financial SOAP WSDL: %s", financial_wsdl_url)

    financial_client = FinancialClient(financial_wsdl_url)
    customer_db = CustomerDbClient(customer_db_host, customer_db_port)
    product_db = ProductDbClient(product_db_host, product_db_port)
    shared_service = BuyerService(customer_db, product_db, financial_client=financial_client)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            customer_db.close()
            product_db.close()

    app = FastAPI(title="Marketplace Buyer Backend", lifespan=lifespan)

    def get_service() -> BuyerService:
        return shared_service

    def require_session_actor(
        service: BuyerService = Depends(get_service),
        x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
    ) -> dict[str, Any]:
        if not x_session_id:
            raise ApiError(
                status_code=401,
                code="AUTH_REQUIRED",
                message="Must be logged in to perform this action",
            )
        # Return both actor identity and raw session ID for downstream handlers.
        buyer = service.validate_buyer_session(x_session_id)
        return {"session_id": x_session_id, "buyer": buyer}

    @app.exception_handler(ApiError)
    async def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Keep contract aligned with the legacy API that returned 400 for bad payloads.
        return JSONResponse(
            status_code=400,
            content={"code": "INVALID_REQUEST", "message": str(exc)},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception in buyers backend: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "Internal server error"},
        )

    @app.post("/accounts")
    def create_account(
        body: CreateAccountRequest,
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.create_account(
            buyer_name=body.buyer_name,
            login_name=body.login_name,
            password=body.password,
        )

    @app.post("/sessions/login")
    def login(
        body: LoginRequest,
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.login(login_name=body.login_name, password=body.password)

    @app.post("/sessions/logout")
    def logout(
        context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.logout(session_id=context["session_id"])

    @app.get("/items/search")
    def search_items(
        item_category: int = Query(...),
        keywords: list[str] | None = Query(default=None),
        _context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.search_items(item_category=item_category, keywords=keywords or [])

    @app.get("/items/{item_category}/{item_id}")
    def get_item(
        item_category: int,
        item_id: int,
        _context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.get_item(item_category=item_category, item_id=item_id)

    @app.post("/cart/items")
    def add_cart_item(
        body: AddCartItemRequest,
        context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.add_to_cart(
            session_id=context["session_id"],
            item_category=body.item_category,
            item_id=body.item_id,
            quantity=body.quantity,
        )

    @app.delete("/cart/items/{item_category}/{item_id}")
    def remove_cart_item(
        item_category: int,
        item_id: int,
        quantity: int = Query(1),
        context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.remove_from_cart(
            session_id=context["session_id"],
            item_category=item_category,
            item_id=item_id,
            quantity=quantity,
        )

    @app.get("/cart")
    def display_cart(
        context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.display_cart(session_id=context["session_id"])

    @app.post("/cart/save")
    def save_cart(
        context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.save_cart(
            session_id=context["session_id"],
            buyer=context["buyer"],
        )

    @app.delete("/cart")
    def clear_cart(
        context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.clear_cart(session_id=context["session_id"])

    @app.post("/feedback")
    def provide_feedback(
        body: FeedbackRequest,
        context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.provide_feedback(
            buyer=context["buyer"],
            item_category=body.item_category,
            item_id=body.item_id,
            vote=body.vote,
        )

    @app.get("/sellers/{seller_id}/rating")
    def get_seller_rating(
        seller_id: int,
        _context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.get_seller_rating(seller_id=seller_id)

    @app.get("/purchases")
    def get_purchases(
        context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.get_purchases(buyer=context["buyer"])

    @app.post("/purchases")
    def make_purchase(
        body: MakePurchaseRequest,
        context: dict[str, Any] = Depends(require_session_actor),
        service: BuyerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.make_purchase(
            session_id=context["session_id"],
            buyer=context["buyer"],
            card_holder_name=body.card_holder_name,
            credit_card_number=body.credit_card_number,
            expiration_date=body.expiration_date,
            security_code=body.security_code,
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "backend-buyers"}

    return app


def main():
    parser = argparse.ArgumentParser(description="Backend Buyers FastAPI Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8004, help="Port to bind to")
    parser.add_argument(
        "--customer-db-host", default="localhost", help="Customer DB host"
    )
    parser.add_argument(
        "--customer-db-port", type=int, default=8001, help="Customer DB port"
    )
    parser.add_argument(
        "--product-db-host", default="localhost", help="Product DB host"
    )
    parser.add_argument(
        "--product-db-port", type=int, default=8002, help="Product DB port"
    )
    parser.add_argument(
        "--financial-wsdl-url",
        default="http://localhost:8005/?wsdl",
        help="Financial transactions SOAP WSDL URL",
    )
    args = parser.parse_args()

    app = create_app(
        customer_db_host=args.customer_db_host,
        customer_db_port=args.customer_db_port,
        product_db_host=args.product_db_host,
        product_db_port=args.product_db_port,
        financial_wsdl_url=args.financial_wsdl_url,
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
