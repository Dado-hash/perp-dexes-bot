"""
GRVT Service API
Exposes GRVT exchange functionality via REST API.
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

from exchanges.grvt import GrvtClient

app = FastAPI(title="GRVT Service API")

# Global GRVT client instance
grvt_client: Optional[GrvtClient] = None


class Config:
    """Config wrapper for GRVT client."""
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
    """Initialize GRVT client."""
    global grvt_client

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
        grvt_client = GrvtClient(config)

        # Get contract info
        contract_id, tick_size = await grvt_client.get_contract_attributes()

        return {
            "success": True,
            "contract_id": contract_id,
            "tick_size": str(tick_size)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/connect")
async def connect():
    """Connect to GRVT WebSocket."""
    global grvt_client

    if not grvt_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        await grvt_client.connect()
        return {"success": True, "message": "Connected to GRVT"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/bbo/{contract_id}")
async def get_bbo(contract_id: str):
    """Get best bid/offer prices."""
    global grvt_client

    if not grvt_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        best_bid, best_ask = await grvt_client.fetch_bbo_prices(contract_id)
        return {
            "success": True,
            "best_bid": str(best_bid),
            "best_ask": str(best_ask)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/order/open")
async def place_open_order(request: OrderRequest):
    """Place an open order."""
    global grvt_client

    if not grvt_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        order_result = await grvt_client.place_open_order(
            contract_id=request.contract_id,
            quantity=Decimal(str(request.quantity)),
            direction=request.direction
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


@app.post("/order/close")
async def place_close_order(request: CloseOrderRequest):
    """Place a close order."""
    global grvt_client

    if not grvt_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        order_result = await grvt_client.place_close_order(
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
    global grvt_client

    if not grvt_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        cancel_result = await grvt_client.cancel_order(request.order_id)

        return {
            "success": cancel_result.success,
            "error_message": cancel_result.error_message
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/order/{order_id}")
async def get_order_info(order_id: str):
    """Get order information."""
    global grvt_client

    if not grvt_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        order_info = await grvt_client.get_order_info(order_id=order_id)

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
    global grvt_client

    if not grvt_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        orders = await grvt_client.get_active_orders(contract_id)

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
    global grvt_client

    if not grvt_client:
        raise HTTPException(status_code=400, detail="Client not initialized")

    try:
        position = await grvt_client.get_account_positions()

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
        "service": "grvt",
        "initialized": grvt_client is not None
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
