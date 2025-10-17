"""
Paradex Service API
Exposes Paradex exchange functionality via REST API.
"""

import os
import asyncio
from decimal import Decimal
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

# Add parent directory to path to import exchanges module
import sys
sys.path.append('/app')

from exchanges.paradex import ParadexClient

app = FastAPI(title="Paradex Service API")

# Global Paradex client instance
paradex_client: Optional[ParadexClient] = None


class Config:
    """Config wrapper for Paradex client."""
    def __init__(self, config_dict):
        for key, value in config_dict.items():
            setattr(self, key, value)


class InitRequest(BaseModel):
    ticker: str
    quantity: float
    direction: str = "buy"


class OrderRequest(BaseModel):
    contract_id: str
    quantity: float
    direction: str


class CloseOrderRequest(BaseModel):
    contract_id: str
    quantity: float
    price: float
    side: str


class CancelOrderRequest(BaseModel):
    order_id: str


@app.post("/init")
async def initialize(request: InitRequest):
    """Initialize Paradex client."""
    global paradex_client

    try:
        config_dict = {
            'ticker': request.ticker,
            'contract_id': '',
            'quantity': Decimal(str(request.quantity)),
            'tick_size': Decimal('0.01'),
            'direction': request.direction,
            'close_order_side': 'sell' if request.direction == 'buy' else 'buy'
        }

        config = Config(config_dict)
        paradex_client = ParadexClient(config)

        # Get contract info
        contract_id, tick_size = await paradex_client.get_contract_attributes()

        return {
            "success": True,
            "contract_id": contract_id,
            "tick_size": str(tick_size)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/connect")
async def connect():
    """Connect to Paradex WebSocket."""
    global paradex_client

    if not paradex_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        await paradex_client.connect()
        return {"success": True, "message": "Connected to Paradex"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/bbo/{contract_id}")
async def get_bbo(contract_id: str):
    """Get best bid/offer prices."""
    global paradex_client

    if not paradex_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        best_bid, best_ask = await paradex_client.fetch_bbo_prices(contract_id)
        return {
            "success": True,
            "best_bid": str(best_bid),
            "best_ask": str(best_ask)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/order/open")
async def place_open_order(request: OrderRequest):
    """Place a MARKET order on Paradex for immediate execution."""
    global paradex_client

    if not paradex_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        # Import Paradex SDK classes
        from paradex_py.common.order import Order, OrderType, OrderSide
        from decimal import ROUND_HALF_UP

        # Determine order side
        if request.direction.lower() == 'buy':
            order_side = OrderSide.Buy
        else:
            order_side = OrderSide.Sell

        # Create MARKET order using Paradex SDK
        order = Order(
            market=request.contract_id,
            order_type=OrderType.Market,  # Use native market order type
            order_side=order_side,
            size=Decimal(str(request.quantity)).quantize(
                paradex_client.order_size_increment,
                rounding=ROUND_HALF_UP
            )
        )

        # Submit order directly via Paradex API client
        order_result = paradex_client.paradex.api_client.submit_order(order)

        # Extract order ID
        order_id = order_result.get('id')
        if not order_id:
            return {
                "success": False,
                "error_message": "No order ID in response"
            }

        # Wait a moment for order to process
        await asyncio.sleep(0.5)

        return {
            "success": True,
            "order_id": order_id,
            "side": request.direction,
            "size": str(request.quantity),
            "price": None,  # Market orders don't have a limit price
            "status": order_result.get('status', 'NEW'),
            "error_message": None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/order/close")
async def place_close_order(request: CloseOrderRequest):
    """Place a close order."""
    global paradex_client

    if not paradex_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        order_result = await paradex_client.place_close_order(
            contract_id=request.contract_id,
            quantity=Decimal(str(request.quantity)),
            price=Decimal(str(request.price)),
            side=request.side
        )

        return {
            "success": order_result.success,
            "order_id": order_result.order_id,
            "side": order_result.side,
            "size": str(order_result.size) if order_result.size else None,
            "price": str(order_result.price) if order_result.price else None,
            "status": order_result.status,
            "error_message": order_result.error_message
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/order/cancel")
async def cancel_order(request: CancelOrderRequest):
    """Cancel an order."""
    global paradex_client

    if not paradex_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        cancel_result = await paradex_client.cancel_order(request.order_id)

        return {
            "success": cancel_result.success,
            "error_message": cancel_result.error_message
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/order/{order_id}")
async def get_order_info(order_id: str):
    """Get order information."""
    global paradex_client

    if not paradex_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        order_info = await paradex_client.get_order_info(order_id=order_id)

        if not order_info:
            return {"success": False, "error": "Order not found"}

        return {
            "success": True,
            "order_id": order_info.order_id,
            "side": order_info.side,
            "size": str(order_info.size),
            "price": str(order_info.price),
            "status": order_info.status,
            "filled_size": str(order_info.filled_size),
            "remaining_size": str(order_info.remaining_size)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/orders/active/{contract_id}")
async def get_active_orders(contract_id: str):
    """Get active orders for a contract."""
    global paradex_client

    if not paradex_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        orders = await paradex_client.get_active_orders(contract_id)

        return {
            "success": True,
            "orders": [
                {
                    "order_id": order.order_id,
                    "side": order.side,
                    "size": str(order.size),
                    "price": str(order.price),
                    "status": order.status,
                    "filled_size": str(order.filled_size),
                    "remaining_size": str(order.remaining_size)
                }
                for order in orders
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/position")
async def get_position():
    """Get account position."""
    global paradex_client

    if not paradex_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        position = await paradex_client.get_account_positions()

        return {
            "success": True,
            "position": str(position)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "paradex",
        "initialized": paradex_client is not None
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
