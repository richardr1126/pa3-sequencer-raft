"""Backend sellers FastAPI server."""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Generator

import uvicorn
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backends.common.errors import ApiError
from backends.common.db_client import CustomerDbClient, ProductDbClient
from backends.sellers.api import SellerService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


class CreateAccountRequest(BaseModel):
    seller_name: str
    login_name: str
    password: str


class LoginRequest(BaseModel):
    login_name: str
    password: str


class RegisterItemRequest(BaseModel):
    item_name: str
    item_category: int
    keywords: list[str] = Field(default_factory=list)
    condition: str
    sale_price: float
    quantity: int


class ChangePriceRequest(BaseModel):
    sale_price: float


class UpdateQuantityRequest(BaseModel):
    quantity: int | None = None
    quantity_delta: int | None = None


def create_app(
    customer_db_host: str,
    customer_db_port: int,
    product_db_host: str,
    product_db_port: int,
) -> FastAPI:
    """Create FastAPI app for sellers backend."""
    logger.info(f"  Customer DB: {customer_db_host}:{customer_db_port}")
    logger.info(f"  Product DB: {product_db_host}:{product_db_port}")

    app = FastAPI(title="Marketplace Seller Backend")

    def get_service() -> Generator[SellerService, None, None]:
        # Keep request-scoped DB clients so each request has a clean transport lifecycle.
        customer_db = CustomerDbClient(customer_db_host, customer_db_port)
        product_db = ProductDbClient(product_db_host, product_db_port)
        service = SellerService(customer_db, product_db)
        try:
            yield service
        finally:
            customer_db.close()
            product_db.close()

    def require_session_actor(
        service: SellerService = Depends(get_service),
        x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
    ) -> dict[str, Any]:
        if not x_session_id:
            raise ApiError(
                status_code=401,
                code="AUTH_REQUIRED",
                message="Must be logged in to perform this action",
            )
        # Return both actor identity and raw session ID for downstream handlers.
        seller = service.validate_seller_session(x_session_id)
        return {"session_id": x_session_id, "seller": seller}

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
        logger.exception("Unhandled exception in sellers backend: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "Internal server error"},
        )

    @app.post("/accounts")
    def create_account(
        body: CreateAccountRequest,
        service: SellerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.create_account(
            seller_name=body.seller_name,
            login_name=body.login_name,
            password=body.password,
        )

    @app.post("/sessions/login")
    def login(
        body: LoginRequest,
        service: SellerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.login(login_name=body.login_name, password=body.password)

    @app.post("/sessions/logout")
    def logout(
        context: dict[str, Any] = Depends(require_session_actor),
        service: SellerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.logout(session_id=context["session_id"])

    @app.get("/me/rating")
    def get_rating(
        context: dict[str, Any] = Depends(require_session_actor),
        service: SellerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.get_rating(seller=context["seller"])

    @app.post("/items")
    def register_item(
        body: RegisterItemRequest,
        context: dict[str, Any] = Depends(require_session_actor),
        service: SellerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.register_item(
            seller=context["seller"],
            item_name=body.item_name,
            item_category=body.item_category,
            keywords=body.keywords,
            condition=body.condition,
            sale_price=body.sale_price,
            quantity=body.quantity,
        )

    @app.patch("/items/{item_category}/{item_id}/price")
    def change_price(
        item_category: int,
        item_id: int,
        body: ChangePriceRequest,
        context: dict[str, Any] = Depends(require_session_actor),
        service: SellerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.change_price(
            seller=context["seller"],
            item_category=item_category,
            item_id=item_id,
            sale_price=body.sale_price,
        )

    @app.patch("/items/{item_category}/{item_id}/quantity")
    def update_quantity(
        item_category: int,
        item_id: int,
        body: UpdateQuantityRequest,
        context: dict[str, Any] = Depends(require_session_actor),
        service: SellerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.update_units(
            seller=context["seller"],
            item_category=item_category,
            item_id=item_id,
            quantity=body.quantity,
            quantity_delta=body.quantity_delta,
        )

    @app.get("/items")
    def display_items(
        context: dict[str, Any] = Depends(require_session_actor),
        service: SellerService = Depends(get_service),
    ) -> dict[str, Any]:
        return service.display_items(seller=context["seller"])

    return app


def main():
    parser = argparse.ArgumentParser(description="Backend Sellers FastAPI Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8003, help="Port to bind to")
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
    args = parser.parse_args()

    app = create_app(
        customer_db_host=args.customer_db_host,
        customer_db_port=args.customer_db_port,
        product_db_host=args.product_db_host,
        product_db_port=args.product_db_port,
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
