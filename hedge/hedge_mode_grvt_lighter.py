"""
GRVT-Lighter Hedge Mode

This hedge mode pairs maker limit orders on GRVT with aggressive orders on Lighter
so that every exposure opened on GRVT is immediately hedged on Lighter.

The implementation follows the overall structure of the other hedge mode scripts
in the repository and reuses the native exchange clients (no Docker services).
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
from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple, Optional
from datetime import datetime

import pytz

# Make project modules importable when the script is executed directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers.telegram_bot import TelegramBot
from exchanges.grvt import GrvtClient
from exchanges.lighter import LighterClient


class Config:
    """Simple attribute-style wrapper over a dictionary for exchange configs."""

    def __init__(self, config_dict):
        for key, value in config_dict.items():
            setattr(self, key, value)


class HedgeBot:
    """Hedge bot that makes on GRVT and hedges on Lighter."""

    def __init__(self, ticker: str, order_quantity: Decimal, fill_timeout: int = 5, iterations: int = 20):
        self.ticker = ticker
        self.order_quantity = order_quantity
        self.fill_timeout = fill_timeout
        self.iterations = iterations

        # Exchange clients
        self.grvt_client: Optional[GrvtClient] = None
        self.lighter_client: Optional[LighterClient] = None

        # Exchange specific state
        self.grvt_contract_id: Optional[str] = None
        self.grvt_tick_size: Optional[Decimal] = None
        self.lighter_contract_id: Optional[int] = None
        self.lighter_tick_size: Optional[Decimal] = None

        # Position tracking
        self.grvt_position = Decimal("0")  # Signed net exposure on GRVT
        self.lighter_position = Decimal("0")  # Signed net exposure on Lighter

        # Control flags
        self.stop_flag = False

        # Logging / persistence
        os.makedirs("logs", exist_ok=True)
        self.log_filename = f"logs/grvt_{ticker}_lighter_hedge_mode_log.txt"
        self.csv_filename = f"logs/grvt_{ticker}_lighter_hedge_mode_trades.csv"

        self._initialize_csv_file()
        self.logger = logging.getLogger(f"hedge_bot_grvt_lighter_{ticker}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        file_handler = logging.FileHandler(self.log_filename)
        file_handler.setLevel(logging.INFO)
        file_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        file_handler.setFormatter(file_formatter)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
        console_handler.setFormatter(console_formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.propagate = False

        # Execution tuning
        self.lighter_aggressive_slippage = Decimal(os.getenv("LIGHTER_LIMIT_SLIPPAGE", "0.002"))

    # Telegram notifications (optional)
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.telegram_bot = None
        if telegram_token and telegram_chat_id:
            try:
                self.telegram_bot = TelegramBot(telegram_token, telegram_chat_id)
                self.logger.info("✅ Telegram notifications enabled")
            except Exception as exc:  # pragma: no cover - optional integration
                self.logger.warning(f"⚠️ Failed to initialise Telegram bot: {exc}")

    # ------------------------
    # Lifecycle helpers
    # ------------------------

    def shutdown(self, signum=None, frame=None):  # pragma: no cover - signal handler
        """Graceful shutdown handler."""
        if self.stop_flag:
            return

        self.logger.info("\n🛑 Stopping hedge bot...")
        self.stop_flag = True

        if self.telegram_bot:
            try:
                self.telegram_bot.close()
            except Exception:
                pass

        for handler in self.logger.handlers[:]:
            try:
                handler.close()
                self.logger.removeHandler(handler)
            except Exception:
                pass

    def setup_signal_handlers(self):  # pragma: no cover - signal handler wiring
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def _initialize_csv_file(self):
        if not os.path.exists(self.csv_filename):
            with open(self.csv_filename, "w", newline="") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(["exchange", "timestamp", "side", "price", "quantity"])

    def log_trade_to_csv(self, exchange: str, side: str, price: Decimal, quantity: Decimal):
        timestamp = datetime.now(pytz.UTC).isoformat()
        with open(self.csv_filename, "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([exchange, timestamp, side, str(price), str(quantity)])
        self.logger.info(f"📊 Trade logged: {exchange} {side} {quantity} @ {price}")

    def send_telegram_notification(self, message: str):
        if self.telegram_bot:
            try:
                self.telegram_bot.send_text(message)
            except Exception:
                self.logger.debug("Unable to send Telegram message", exc_info=True)

    # ------------------------
    # Exchange initialisation
    # ------------------------

    async def initialize_grvt_client(self):
        """Initialise GRVT client and fetch contract metadata."""
        config_dict = {
            "ticker": self.ticker,
            "contract_id": "",
            "quantity": self.order_quantity,
            "tick_size": Decimal("0.01"),
            "direction": "sell",  # start with a short on GRVT
            "close_order_side": "buy",
        }
        config = Config(config_dict)
        self.grvt_client = GrvtClient(config)
        self.grvt_contract_id, self.grvt_tick_size = await self.grvt_client.get_contract_attributes()
        await self.grvt_client.connect()
        self.logger.info(
            "✅ GRVT initialised - contract %s tick_size %s",
            self.grvt_contract_id,
            self.grvt_tick_size,
        )

    async def initialize_lighter_client(self):
        """Initialise Lighter client and fetch contract metadata."""
        config_dict = {
            "ticker": self.ticker,
            "contract_id": 0,
            "quantity": self.order_quantity,
            "tick_size": Decimal("0.01"),
            "direction": "buy",  # hedge leg opens long on Lighter
            "close_order_side": "sell",
        }
        config = Config(config_dict)
        self.lighter_client = LighterClient(config)
        # get_contract_attributes sets contract_id and tick size internally
        self.lighter_contract_id, self.lighter_tick_size = await self.lighter_client.get_contract_attributes()
        await self.lighter_client.connect()
        self.logger.info(
            "✅ Lighter initialised - market %s tick_size %s",
            self.lighter_contract_id,
            self.lighter_tick_size,
        )

    # ------------------------
    # Helper utilities
    # ------------------------

    @staticmethod
    def _safe_decimal(value: Optional[object]) -> Decimal:
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _round_to_tick(price: Decimal, tick_size: Optional[Decimal]) -> Decimal:
        if tick_size is None or tick_size <= 0:
            return price
        return (price / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_size

    async def _fetch_grvt_signed_position(self) -> Decimal:
        """Best-effort fetch of current GRVT net position using the SDK."""
        if not self.grvt_client or not getattr(self.grvt_client, "rest_client", None):
            return self.grvt_position

        try:
            raw_positions = await asyncio.to_thread(self.grvt_client.rest_client.fetch_positions)
        except Exception as exc:
            self.logger.warning(f"⚠️ Unable to fetch GRVT positions: {exc}")
            return self.grvt_position

        if not raw_positions:
            return Decimal("0")

        for entry in raw_positions:
            if entry.get("instrument") != self.grvt_contract_id:
                continue

            size = self._safe_decimal(entry.get("size") or entry.get("position") or entry.get("net_position") or 0)
            if size == 0:
                continue

            side = entry.get("side") or entry.get("direction")
            if side:
                side_str = str(side).lower()
                if side_str in {"buy", "long"}:
                    return size
                if side_str in {"sell", "short"}:
                    return -size

            if "is_buying_asset" in entry:
                return size if entry["is_buying_asset"] else -size

            if "is_long" in entry:
                return size if entry["is_long"] else -size

            signed_size = entry.get("signed_size") or entry.get("net_size")
            if signed_size is not None:
                return self._safe_decimal(signed_size)

            # Fallback: assume positive size denotes long
            return size

        return Decimal("0")

    async def sync_initial_positions(self):
        """Retrieve starting positions from exchanges before trading."""
        if self.grvt_client:
            try:
                self.grvt_position = await self._fetch_grvt_signed_position()
                if self.grvt_position != 0:
                    self.logger.info("ℹ️ Detected existing GRVT net position: %s", self.grvt_position)
            except Exception as exc:
                self.logger.warning(f"⚠️ Unable to determine initial GRVT position: {exc}")

        if self.lighter_client:
            try:
                self.lighter_position = await self.lighter_client.get_account_positions()
                if self.lighter_position != 0:
                    self.logger.info("ℹ️ Detected existing Lighter net position: %s", self.lighter_position)
            except Exception as exc:
                self.logger.warning(f"⚠️ Unable to determine initial Lighter position: {exc}")

    async def refresh_lighter_position(self):
        """Refresh cached Lighter net position."""
        if not self.lighter_client:
            return
        try:
            self.lighter_position = await self.lighter_client.get_account_positions()
        except Exception as exc:
            self.logger.warning(f"⚠️ Unable to refresh Lighter position: {exc}")

    async def wait_for_grvt_fill(self, order_id: str, timeout: int) -> Tuple[bool, Decimal]:
        """Poll GRVT for fill information until filled or timeout."""
        start = time.time()
        last_status = "UNKNOWN"
        while time.time() - start < timeout and not self.stop_flag:
            try:
                order_info = await self.grvt_client.get_order_info(order_id=order_id)
            except Exception as exc:
                self.logger.error(f"❌ Failed to fetch GRVT order info: {exc}")
                await asyncio.sleep(1)
                continue

            if not order_info:
                self.logger.warning("⚠️ GRVT order info missing, retrying...")
                await asyncio.sleep(0.5)
                continue

            status = order_info.status.upper()
            if status != last_status:
                self.logger.info(f"🔍 GRVT order {order_id} status -> {status}")
                last_status = status

            if status == "FILLED":
                filled_size = order_info.filled_size or order_info.size
                self.log_trade_to_csv("GRVT", order_info.side, order_info.price, filled_size)
                return True, filled_size
            if status in {"CANCELED", "CANCELLED", "REJECTED"}:
                return False, Decimal("0")

            await asyncio.sleep(0.3)

        self.logger.warning(f"⏱️ Timeout waiting for GRVT order {order_id} to fill")
        try:
            await self.grvt_client.cancel_order(order_id)
        except Exception as exc:
            self.logger.error(f"❌ Failed to cancel GRVT order {order_id}: {exc}")
        return False, Decimal("0")

    async def place_grvt_order_with_reprice(self, side: str, quantity: Decimal) -> Tuple[str, Decimal]:
        """Repeatedly place GRVT post-only orders until one fills."""
        attempt = 0
        while not self.stop_flag:
            attempt += 1
            try:
                order_result = await self.grvt_client.place_open_order(
                    contract_id=self.grvt_contract_id,
                    quantity=quantity,
                    direction=side,
                )
            except Exception as exc:
                self.logger.error(f"❌ GRVT order placement failed: {exc}")
                await asyncio.sleep(1)
                continue

            if not order_result.success or not order_result.order_id:
                self.logger.warning("⚠️ GRVT order placement unsuccessful, retrying in 1s")
                await asyncio.sleep(1)
                continue

            order_id = order_result.order_id
            self.logger.info(f"[{order_id}] [GRVT] {side.upper()} attempt #{attempt}")
            filled, filled_size = await self.wait_for_grvt_fill(order_id, self.fill_timeout)
            if filled and filled_size > 0:
                delta = filled_size if side.lower() == "buy" else -filled_size
                self.grvt_position += delta
                return order_id, filled_size

            await asyncio.sleep(0.5)

        raise RuntimeError("Bot stopped before GRVT order filled")

    async def cancel_lighter_orders(self, side: Optional[str] = None):
        """Cancel open Lighter orders, optionally filtered by side."""
        try:
            active_orders = await self.lighter_client.get_active_orders(self.lighter_contract_id)
        except Exception as exc:
            self.logger.error(f"❌ Failed to query Lighter orders: {exc}")
            return

        for order in active_orders:
            if side and order.side.lower() != side.lower():
                continue
            try:
                await self.lighter_client.cancel_order(order.order_id)
                self.logger.debug(f"🧹 Cancelled Lighter order {order.order_id}")
            except Exception as exc:
                self.logger.warning(f"⚠️ Failed to cancel Lighter order {order.order_id}: {exc}")

    async def ensure_lighter_market_fill(self, side: str, quantity: Decimal) -> str:
        """Send aggressively priced order on Lighter until executed."""
        attempt = 0
        while not self.stop_flag:
            attempt += 1
            try:
                best_bid, best_ask = await self.lighter_client.fetch_bbo_prices(self.lighter_contract_id)
            except Exception as exc:
                self.logger.error(f"❌ Unable to fetch Lighter BBO: {exc}")
                await asyncio.sleep(1)
                continue

            side_lower = side.lower()
            if side_lower not in {"buy", "sell"}:
                raise ValueError(f"Unsupported Lighter side: {side}")

            if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
                self.logger.warning("⚠️ Invalid Lighter BBO (bid=%s ask=%s)", best_bid, best_ask)
                await asyncio.sleep(1)
                continue

            if side_lower == "buy":
                price = best_ask * (Decimal("1") + self.lighter_aggressive_slippage)
            else:
                price = best_bid * (Decimal("1") - self.lighter_aggressive_slippage)

            price = self._round_to_tick(price, self.lighter_tick_size)

            # Track position change to confirm fill
            try:
                pre_position = await self.lighter_client.get_account_positions()
            except Exception as exc:
                self.logger.error(f"❌ Unable to read Lighter position before order: {exc}")
                await asyncio.sleep(1)
                continue

            try:
                order_result = await self.lighter_client.place_limit_order(
                    contract_id=self.lighter_contract_id,
                    quantity=quantity,
                    price=price,
                    side=side,
                )
            except Exception as exc:
                self.logger.error(f"❌ Lighter order placement failed: {exc}")
                await asyncio.sleep(1)
                continue

            if not order_result.success:
                self.logger.warning("⚠️ Lighter order unsuccessful; retrying in 1s")
                await asyncio.sleep(1)
                continue

            client_order_id = order_result.order_id or "unknown"
            self.logger.info(f"[{client_order_id}] [LIGHTER] {side.upper()} attempt #{attempt} @ {price}")

            await asyncio.sleep(1)
            try:
                post_position = await self.lighter_client.get_account_positions()
            except Exception as exc:
                self.logger.error(f"❌ Unable to read Lighter position after order: {exc}")
                await asyncio.sleep(1)
                continue

            delta = post_position - pre_position
            expected = quantity if side_lower == "buy" else -quantity
            tolerance = max(Decimal("0.0001"), quantity * Decimal("0.02"))

            if abs(delta - expected) <= tolerance:
                self.lighter_position = post_position
                self.log_trade_to_csv("Lighter", side, price, abs(delta))
                return client_order_id

            self.logger.warning(
                "⚠️ Lighter fill not confirmed (Δ=%s expected %s); cancelling and retrying",
                delta,
                expected,
            )
            await self.cancel_lighter_orders(side)
            await asyncio.sleep(1)

        raise RuntimeError("Bot stopped before Lighter order succeeded")


    # ------------------------
    # Trading loop
    # ------------------------

    async def trading_loop(self):
        self.logger.info("🚀 Starting GRVT-Lighter hedge bot for %s", self.ticker)
        self.send_telegram_notification(
            "🚀 <b>GRVT-Lighter Hedge Bot Started</b>\n\n"
            f"📊 Ticker: <b>{self.ticker}</b>\n"
            f"💰 Size: <b>{self.order_quantity}</b>\n"
            f"🔄 Iterations: <b>{self.iterations}</b>\n"
            f"⏱️ Fill timeout: <b>{self.fill_timeout}s</b>"
        )

        try:
            await self.initialize_grvt_client()
            await self.initialize_lighter_client()
            await self.sync_initial_positions()
        except Exception as exc:
            self.logger.error(f"❌ Failed to initialise clients: {exc}")
            self.send_telegram_notification(
                "🚨 <b>Initialisation failed</b>\n"
                f"<code>{str(exc)[:200]}</code>"
            )
            return

        iterations = 0
        while iterations < self.iterations and not self.stop_flag:
            iterations += 1
            self.logger.info("-----------------------------------------------")
            self.logger.info("🔄 Iteration %s / %s", iterations, self.iterations)
            self.logger.info("-----------------------------------------------")
            self.send_telegram_notification(f"🔄 <b>Iteration {iterations}/{self.iterations}</b>")

            await self.refresh_lighter_position()
            self.logger.info(
                "📊 Positions -> GRVT: %s | Lighter: %s",
                self.grvt_position,
                self.lighter_position,
            )

            exposure_gap = abs(abs(self.grvt_position) - abs(self.lighter_position))
            if exposure_gap > Decimal("0.2"):
                warning = (
                    "⚠️ Position mismatch detected: "
                    f"GRVT {self.grvt_position} / Lighter {self.lighter_position}"
                )
                self.logger.error(warning)
                self.send_telegram_notification(
                    "🚨 <b>Position mismatch</b>\n" + warning
                )
                break

            if self.grvt_position != 0 and self.lighter_position != 0:
                if (self.grvt_position > 0 and self.lighter_position > 0) or (
                    self.grvt_position < 0 and self.lighter_position < 0
                ):
                    self.logger.warning(
                        "⚠️ Both legs share the same exposure direction (GRVT=%s, Lighter=%s)",
                        self.grvt_position,
                        self.lighter_position,
                    )

            try:
                # Step 1: open short on GRVT (maker)
                self.logger.info("[STEP 1] Opening short on GRVT...")
                grvt_open_id, grvt_filled = await self.place_grvt_order_with_reprice("sell", self.order_quantity)
                self.logger.info(f"✅ GRVT short filled {grvt_filled} ({grvt_open_id})")
                self.send_telegram_notification(
                    f"✅ <b>GRVT SELL filled</b>\nSize: <b>{grvt_filled} {self.ticker}</b>"
                )

                # Step 2: hedge on Lighter (market style buy)
                self.logger.info("[STEP 2] Hedging on Lighter (BUY)...")
                lighter_hedge_id = await self.ensure_lighter_market_fill("buy", grvt_filled)
                self.logger.info(f"✅ Lighter hedge filled ({lighter_hedge_id})")
                self.send_telegram_notification(
                    f"✅ <b>Lighter BUY hedge</b>\nSize: <b>{grvt_filled} {self.ticker}</b>"
                )

                await asyncio.sleep(1)

                # Step 3: close short on GRVT (buy maker)
                self.logger.info("[STEP 3] Closing GRVT short...")
                grvt_close_id, grvt_close_filled = await self.place_grvt_order_with_reprice("buy", grvt_filled)
                self.logger.info(f"✅ GRVT cover filled {grvt_close_filled} ({grvt_close_id})")
                self.send_telegram_notification(
                    f"✅ <b>GRVT BUY filled</b>\nSize: <b>{grvt_close_filled} {self.ticker}</b>"
                )

                # Step 4: unwind hedge on Lighter (sell)
                self.logger.info("[STEP 4] Closing Lighter hedge (SELL)...")
                lighter_close_id = await self.ensure_lighter_market_fill("sell", grvt_close_filled)
                self.logger.info(f"✅ Lighter hedge close filled ({lighter_close_id})")
                self.send_telegram_notification(
                    f"✅ <b>Lighter SELL hedge</b>\nSize: <b>{grvt_close_filled} {self.ticker}</b>"
                )

                self.send_telegram_notification(
                    f"🏁 <b>Iteration {iterations}/{self.iterations} complete</b>"
                )
                await asyncio.sleep(3)

            except Exception as exc:
                self.logger.error(f"⚠️ Error inside trading loop: {exc}")
                self.logger.error(traceback.format_exc())
                self.send_telegram_notification(
                    "🚨 <b>Exception in trading loop</b>\n"
                    f"<code>{str(exc)[:200]}</code>"
                )
                break

        self.logger.info("✅ Trading loop finished")
        self.send_telegram_notification(
            "🏁 <b>GRVT-Lighter hedge loop finished</b>\n"
            f"Iterations run: <b>{iterations}</b>"
        )

        # Cleanup connections
        try:
            if self.grvt_client:
                await self.grvt_client.disconnect()
        except Exception as exc:
            self.logger.warning(f"⚠️ Error during GRVT disconnect: {exc}")

        try:
            if self.lighter_client:
                await self.lighter_client.disconnect()
        except Exception as exc:
            self.logger.warning(f"⚠️ Error during Lighter disconnect: {exc}")

        # Allow underlying SDK tasks a brief window to shut down cleanly
        await asyncio.sleep(0.25)

    async def run(self):
        self.setup_signal_handlers()
        try:
            await self.trading_loop()
        except KeyboardInterrupt:  # pragma: no cover - manual stop
            self.logger.info("\n🛑 Interrupted by user")
        finally:
            if self.telegram_bot:
                try:
                    self.telegram_bot.close()
                except Exception:
                    pass


def parse_arguments():
    parser = argparse.ArgumentParser(description="GRVT-Lighter Hedge Bot")
    parser.add_argument("--exchange", type=str, help="Exchange (should be grvt)")
    parser.add_argument("--ticker", type=str, default="BTC", help="Underlying ticker symbol")
    parser.add_argument("--size", type=str, required=True, help="Order size per leg")
    parser.add_argument("--iter", type=int, required=True, help="Number of iterations to run")
    parser.add_argument("--fill-timeout", type=int, default=5, help="Maker fill timeout in seconds")
    return parser.parse_args()


async def main():
    args = parse_arguments()
    bot = HedgeBot(
        ticker=args.ticker.upper(),
        order_quantity=Decimal(args.size),
        fill_timeout=args.fill_timeout,
        iterations=args.iter,
    )
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
