"""
GRVT-Paradex Hedge Mode (Docker-based)

This version communicates with GRVT and Paradex services running in Docker containers
to avoid dependency conflicts between the two SDKs.

Prerequisites:
- Docker and Docker Compose installed
- Services running: docker-compose up -d
- GRVT service on http://localhost:8001
- Paradex service on http://localhost:8002
"""

import asyncio
import signal
import logging
import os
import sys
import time
import argparse
import traceback
import csv
import httpx
from decimal import Decimal
from typing import Tuple, Optional, Dict, Any
from datetime import datetime
import pytz


class HedgeBot:
    """Trading bot that places post-only limit orders on GRVT and hedges with market orders on Paradex using Docker services."""

    def __init__(self, ticker: str, order_quantity: Decimal, fill_timeout: int = 5, iterations: int = 20):
        self.ticker = ticker
        self.order_quantity = order_quantity
        self.fill_timeout = fill_timeout
        self.paradex_order_filled = False
        self.iterations = iterations
        self.grvt_position = Decimal('0')
        self.paradex_position = Decimal('0')

        # Docker service URLs
        self.grvt_service_url = os.getenv('GRVT_SERVICE_URL', 'http://localhost:8001')
        self.paradex_service_url = os.getenv('PARADEX_SERVICE_URL', 'http://localhost:8002')

        # Initialize logging
        os.makedirs("logs", exist_ok=True)
        self.log_filename = f"logs/grvt_{ticker}_hedge_mode_docker_log.txt"
        self.csv_filename = f"logs/grvt_{ticker}_hedge_mode_docker_trades.csv"

        # Initialize CSV file
        self._initialize_csv_file()

        # Setup logger
        self.logger = logging.getLogger(f"hedge_bot_{ticker}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        # Disable verbose logging
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('httpx').setLevel(logging.WARNING)

        # Create handlers
        file_handler = logging.FileHandler(self.log_filename)
        file_handler.setLevel(logging.INFO)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)

        # Formatters
        file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_formatter = logging.Formatter('%(levelname)s:%(name)s:%(message)s')

        file_handler.setFormatter(file_formatter)
        console_handler.setFormatter(console_formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.propagate = False

        # State management
        self.stop_flag = False
        self.order_counter = 0

        # GRVT state
        self.grvt_contract_id = None
        self.grvt_tick_size = None
        self.grvt_order_status = None

        # Paradex state
        self.paradex_contract_id = None
        self.paradex_tick_size = None
        self.paradex_order_status = None

        # Strategy state
        self.waiting_for_paradex_fill = False
        self.order_execution_complete = False

        # Current order details
        self.current_paradex_side = None
        self.current_paradex_quantity = None
        self.current_paradex_price = None

        # HTTP client
        self.http_client = httpx.AsyncClient(timeout=30.0)

    def shutdown(self, signum=None, frame=None):
        """Graceful shutdown handler."""
        self.stop_flag = True
        self.logger.info("\n🛑 Stopping...")

        # Close logging handlers
        for handler in self.logger.handlers[:]:
            try:
                handler.close()
                self.logger.removeHandler(handler)
            except Exception:
                pass

    def _initialize_csv_file(self):
        """Initialize CSV file with headers if it doesn't exist."""
        if not os.path.exists(self.csv_filename):
            with open(self.csv_filename, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['exchange', 'timestamp', 'side', 'price', 'quantity'])

    def log_trade_to_csv(self, exchange: str, side: str, price: str, quantity: str):
        """Log trade details to CSV file."""
        timestamp = datetime.now(pytz.UTC).isoformat()

        with open(self.csv_filename, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([exchange, timestamp, side, price, quantity])

        self.logger.info(f"📊 Trade logged to CSV: {exchange} {side} {quantity} @ {price}")

    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    async def check_grvt_health(self) -> bool:
        """Check if GRVT service is healthy."""
        try:
            response = await self.http_client.get(f"{self.grvt_service_url}/health")
            return response.status_code == 200
        except Exception as e:
            self.logger.error(f"GRVT service health check failed: {e}")
            return False

    async def check_paradex_health(self) -> bool:
        """Check if Paradex service is healthy."""
        try:
            response = await self.http_client.get(f"{self.paradex_service_url}/health")
            return response.status_code == 200
        except Exception as e:
            self.logger.error(f"Paradex service health check failed: {e}")
            return False

    async def initialize_grvt_client(self):
        """Initialize GRVT client via Docker service."""
        try:
            response = await self.http_client.post(
                f"{self.grvt_service_url}/init",
                json={
                    "ticker": self.ticker,
                    "quantity": float(self.order_quantity),
                    "direction": "buy"
                }
            )
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                self.grvt_contract_id = result["contract_id"]
                self.grvt_tick_size = Decimal(result["tick_size"])
                self.logger.info("✅ GRVT client initialized successfully")
                return True
            else:
                self.logger.error("❌ GRVT initialization failed")
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize GRVT: {e}")
            return False

    async def initialize_paradex_client(self):
        """Initialize Paradex client via Docker service."""
        try:
            response = await self.http_client.post(
                f"{self.paradex_service_url}/init",
                json={
                    "ticker": self.ticker,
                    "quantity": float(self.order_quantity),
                    "direction": "buy"
                }
            )
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                self.paradex_contract_id = result["contract_id"]
                self.paradex_tick_size = Decimal(result["tick_size"])
                self.logger.info("✅ Paradex client initialized successfully")
                return True
            else:
                self.logger.error("❌ Paradex initialization failed")
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Paradex: {e}")
            return False

    async def connect_grvt(self):
        """Connect to GRVT WebSocket via Docker service."""
        try:
            response = await self.http_client.post(f"{self.grvt_service_url}/connect")
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                self.logger.info("✅ GRVT WebSocket connected")
                return True
            return False

        except Exception as e:
            self.logger.error(f"❌ Failed to connect GRVT WebSocket: {e}")
            return False

    async def connect_paradex(self):
        """Connect to Paradex WebSocket via Docker service."""
        try:
            response = await self.http_client.post(f"{self.paradex_service_url}/connect")
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                self.logger.info("✅ Paradex WebSocket connected")
                return True
            return False

        except Exception as e:
            self.logger.error(f"❌ Failed to connect Paradex WebSocket: {e}")
            return False

    async def fetch_grvt_bbo(self) -> Tuple[Decimal, Decimal]:
        """Fetch best bid/offer from GRVT."""
        try:
            response = await self.http_client.get(f"{self.grvt_service_url}/bbo/{self.grvt_contract_id}")
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                return Decimal(result["best_bid"]), Decimal(result["best_ask"])
            raise ValueError("Failed to get BBO")

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch GRVT BBO: {e}")
            raise

    async def fetch_paradex_bbo(self) -> Tuple[Decimal, Decimal]:
        """Fetch best bid/offer from Paradex."""
        try:
            response = await self.http_client.get(f"{self.paradex_service_url}/bbo/{self.paradex_contract_id}")
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                return Decimal(result["best_bid"]), Decimal(result["best_ask"])
            raise ValueError("Failed to get BBO")

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch Paradex BBO: {e}")
            raise

    async def place_grvt_open_order(self, side: str, quantity: Decimal) -> Optional[str]:
        """Place an open order on GRVT."""
        try:
            response = await self.http_client.post(
                f"{self.grvt_service_url}/order/open",
                json={
                    "contract_id": self.grvt_contract_id,
                    "quantity": float(quantity),
                    "direction": side
                }
            )
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                order_id = result["order_id"]
                self.logger.info(f"[{order_id}] [OPEN] [GRVT] [{side.upper()}] Order placed: {quantity}")
                return order_id
            else:
                self.logger.error(f"❌ GRVT order failed: {result.get('error_message')}")
                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to place GRVT order: {e}")
            return None

    async def place_paradex_market_order(self, side: str, quantity: Decimal) -> Optional[str]:
        """Place a market-like order on Paradex."""
        try:
            response = await self.http_client.post(
                f"{self.paradex_service_url}/order/open",
                json={
                    "contract_id": self.paradex_contract_id,
                    "quantity": float(quantity),
                    "direction": side
                }
            )
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                order_id = result["order_id"]
                self.logger.info(f"[{order_id}] [HEDGE] [Paradex] [{side.upper()}] Order placed: {quantity}")
                return order_id
            else:
                self.logger.error(f"❌ Paradex order failed: {result.get('error_message')}")
                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to place Paradex order: {e}")
            return None

    async def get_grvt_position(self) -> Decimal:
        """Get GRVT position."""
        try:
            response = await self.http_client.get(f"{self.grvt_service_url}/position")
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                return Decimal(result["position"])
            return Decimal('0')

        except Exception as e:
            self.logger.error(f"❌ Failed to get GRVT position: {e}")
            return Decimal('0')

    async def get_paradex_position(self) -> Decimal:
        """Get Paradex position."""
        try:
            response = await self.http_client.get(f"{self.paradex_service_url}/position")
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                return Decimal(result["position"])
            return Decimal('0')

        except Exception as e:
            self.logger.error(f"❌ Failed to get Paradex position: {e}")
            return Decimal('0')

    async def wait_for_grvt_fill(self, order_id: str, timeout: int = 30) -> bool:
        """
        Wait for GRVT order to be filled.
        Returns True if filled, False if timeout or cancelled.
        """
        start_time = time.time()
        self.logger.info(f"⏳ Waiting for GRVT order {order_id} to fill (timeout: {timeout}s)...")

        while time.time() - start_time < timeout:
            try:
                # Query order status
                response = await self.http_client.get(f"{self.grvt_service_url}/order/{order_id}")
                response.raise_for_status()
                result = response.json()

                if result.get("success"):
                    status = result.get("status")
                    filled_size = Decimal(result.get("filled_size", "0"))
                    total_size = Decimal(result.get("size", "0"))

                    if status == "FILLED":
                        self.logger.info(f"✅ GRVT order {order_id} FILLED: {filled_size}")
                        # Update position
                        side = result.get("side")
                        if side == "buy":
                            self.grvt_position += filled_size
                        else:
                            self.grvt_position -= filled_size
                        return True

                    elif status in ["CANCELED", "CANCELLED", "REJECTED"]:
                        self.logger.warning(f"⚠️ GRVT order {order_id} was {status}")
                        return False

                    elif status == "PARTIALLY_FILLED":
                        self.logger.info(f"🔄 GRVT order {order_id} partially filled: {filled_size}/{total_size}")
                        # Continue waiting

                await asyncio.sleep(0.5)  # Poll every 500ms

            except Exception as e:
                self.logger.error(f"❌ Error checking GRVT order status: {e}")
                await asyncio.sleep(1)

        # Timeout reached
        self.logger.error(f"⏱️ Timeout waiting for GRVT order {order_id} to fill after {timeout}s")

        # Try to cancel the unfilled order
        try:
            self.logger.info(f"🚫 Attempting to cancel unfilled order {order_id}...")
            cancel_response = await self.http_client.post(
                f"{self.grvt_service_url}/order/cancel",
                json={"order_id": order_id}
            )
            if cancel_response.status_code == 200:
                self.logger.info(f"✅ Cancelled unfilled order {order_id}")
        except Exception as e:
            self.logger.error(f"❌ Failed to cancel order: {e}")

        return False

    async def place_order_with_auto_reprice(self, side: str, quantity: Decimal) -> str:
        """
        Place GRVT order with automatic repricing until filled.

        This function will:
        1. Place a limit order at competitive price (best_ask - tick for buy, best_bid + tick for sell)
        2. Wait for fill with configured timeout
        3. If not filled: automatically cancel and replace order at new market price
        4. Repeat until order is filled

        Args:
            side: 'buy' or 'sell'
            quantity: Order quantity

        Returns:
            order_id: The ID of the filled order
        """
        attempt = 0

        while True:
            attempt += 1

            # Log attempt
            if attempt == 1:
                self.logger.info(f"🎯 Placing {side.upper()} order for {quantity} on GRVT...")
            else:
                self.logger.warning(f"🔄 Attempt #{attempt}: Repricing and replacing {side.upper()} order...")

            # Place order at current market price
            order_id = await self.place_grvt_open_order(side, quantity)

            if not order_id:
                self.logger.error(f"❌ Failed to place GRVT order on attempt #{attempt}, retrying in 1s...")
                await asyncio.sleep(1)
                continue

            # Wait for fill
            filled = await self.wait_for_grvt_fill(order_id, timeout=self.fill_timeout)

            if filled:
                self.logger.info(f"✅ Order filled after {attempt} attempt(s)")
                return order_id

            # Not filled - wait_for_grvt_fill already cancelled the order
            self.logger.warning(f"⏱️ Order {order_id} not filled after {self.fill_timeout}s (attempt #{attempt})")
            self.logger.info(f"🔄 Will reprice and replace order at current market price...")

            # Small delay before repricing
            await asyncio.sleep(0.5)

    async def trading_loop(self):
        """Main trading loop implementing the hedge strategy."""
        self.logger.info(f"🚀 Starting GRVT-Paradex hedge bot for {self.ticker}")

        # Check service health
        self.logger.info("🔍 Checking Docker services...")
        grvt_healthy = await self.check_grvt_health()
        paradex_healthy = await self.check_paradex_health()

        if not grvt_healthy:
            self.logger.error("❌ GRVT service is not healthy. Make sure Docker containers are running.")
            self.logger.error("   Run: docker-compose up -d")
            return

        if not paradex_healthy:
            self.logger.error("❌ Paradex service is not healthy. Make sure Docker containers are running.")
            self.logger.error("   Run: docker-compose up -d")
            return

        # Initialize clients
        try:
            await self.initialize_grvt_client()
            await self.initialize_paradex_client()

            self.logger.info(f"Contract info loaded - GRVT: {self.grvt_contract_id}, "
                             f"Paradex: {self.paradex_contract_id}")

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize: {e}")
            return

        # Connect WebSockets
        try:
            await self.connect_grvt()
            await self.connect_paradex()
            await asyncio.sleep(2)  # Wait for connections

        except Exception as e:
            self.logger.error(f"❌ Failed to connect WebSockets: {e}")
            return

        # Main trading loop
        iterations = 0
        while iterations < self.iterations and not self.stop_flag:
            iterations += 1
            self.logger.info("-----------------------------------------------")
            self.logger.info(f"🔄 Trading loop iteration {iterations}/{self.iterations}")
            self.logger.info("-----------------------------------------------")

            # Update positions
            self.grvt_position = await self.get_grvt_position()
            self.paradex_position = await self.get_paradex_position()
            self.logger.info(f"📊 Positions - GRVT: {self.grvt_position} | Paradex: {self.paradex_position}")

            # Check position mismatch
            if abs(self.grvt_position + self.paradex_position) > 0.2:
                self.logger.error(f"❌ Position diff too large: {self.grvt_position + self.paradex_position}")
                break

            try:
                # STEP 1: Open position on GRVT (maker order with auto-repricing)
                self.logger.info("[STEP 1] Opening position on GRVT...")
                grvt_order_id = await self.place_order_with_auto_reprice('buy', self.order_quantity)
                # Note: place_order_with_auto_reprice automatically retries until filled

                # STEP 2: Immediately hedge on Paradex with MARKET order
                self.logger.info("[STEP 2] 🚀 GRVT filled! Immediately hedging on Paradex with MARKET order...")
                paradex_order_id = await self.place_paradex_market_order('sell', self.order_quantity)

                if not paradex_order_id:
                    self.logger.error("❌ Failed to place Paradex hedge order")
                    self.logger.warning("⚠️ DANGER: GRVT position is open but Paradex hedge failed!")
                    break

                # Market orders fill immediately, just wait a moment
                await asyncio.sleep(1)

                # STEP 3: Close position on GRVT (maker order with auto-repricing)
                self.logger.info("[STEP 3] Closing position on GRVT...")
                grvt_close_id = await self.place_order_with_auto_reprice('sell', self.order_quantity)
                # Note: place_order_with_auto_reprice automatically retries until filled

                # STEP 4: Close Paradex hedge with MARKET order
                self.logger.info("[STEP 4] 🚀 Closing Paradex hedge with MARKET order...")
                paradex_close_id = await self.place_paradex_market_order('buy', self.order_quantity)

                if not paradex_close_id:
                    self.logger.error("❌ Failed to close Paradex hedge")
                    break

                # Market order fills immediately
                await asyncio.sleep(1)

                # Wait before next iteration
                self.logger.info("✅ Iteration complete, waiting 3s before next iteration...")
                await asyncio.sleep(3)

            except Exception as e:
                self.logger.error(f"⚠️ Error in trading loop: {e}")
                self.logger.error(f"⚠️ Full traceback: {traceback.format_exc()}")
                break

        self.logger.info("✅ Trading loop completed")

    async def run(self):
        """Run the hedge bot."""
        self.setup_signal_handlers()

        try:
            await self.trading_loop()
        except KeyboardInterrupt:
            self.logger.info("\n🛑 Received interrupt signal...")
        finally:
            self.logger.info("🔄 Cleaning up...")
            await self.http_client.aclose()
            self.shutdown()


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='GRVT-Paradex Hedge Bot (Docker-based)')
    parser.add_argument('--exchange', type=str,
                        help='Exchange (should be "grvt" for this mode)')
    parser.add_argument('--ticker', type=str, default='BTC',
                        help='Ticker symbol (default: BTC)')
    parser.add_argument('--size', type=str, required=True,
                        help='Number of tokens to buy/sell per order')
    parser.add_argument('--iter', type=int, required=True,
                        help='Number of iterations to run')
    parser.add_argument('--fill-timeout', type=int, default=5,
                        help='Timeout in seconds for maker order fills (default: 5)')

    return parser.parse_args()


async def main():
    """Main entry point."""
    args = parse_arguments()

    bot = HedgeBot(
        ticker=args.ticker.upper(),
        order_quantity=Decimal(args.size),
        fill_timeout=args.fill_timeout,
        iterations=args.iter
    )

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
