import logging
import os
import time
from datetime import datetime
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from dlmm import DLMM_CLIENT
from solana.rpc import commitment


from dlmmApp import MeteoraApp


def setup_logger():
    logger = logging.getLogger("LiquiDMon")
    logger.setLevel(logging.DEBUG)

    # File handler for persistent logs
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(
        f"%s/LiquiDMon_GUI_%s.log" % (log_dir, timestamp)
    )
    file_handler.setLevel(logging.DEBUG)

    # Console handler for immediate feedback
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Formatter
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


logger = setup_logger()


class App:
    def __init__(self):
        # TODO: Start JS server by button on selected PORT and also log it's output

        self.private_key = ""
        self.RPC = "https://api.devnet.solana.com"
        self.pool_address = "EoJWgqyS2wSHv11iPes6L3uJcF2qADBxAUijFfiGYTN1"

        self.prev_private_key = None
        self.prev_RPC = None
        self.prev_pool_address = None

        self.StopLoss = 0.0
        self.TakeProfit = 1000.0
        self.bin_num = 69

        self.claim_fee = True
        self.sleep_time = 60

        self.update_state()

    def update_state(self):
        dAddr = self.prev_pool_address != self.pool_address
        dRPC = self.prev_RPC != self.RPC
        dPK = self.prev_private_key != self.private_key

        if dRPC:
            self.client = Client(self.RPC, commitment=commitment.Confirmed)
        if dPK:
            self.user = Keypair.from_base58_string(self.private_key)
        if dAddr or dRPC:
            cluster = "devnet" if "devnet" in self.RPC else "mainnet-beta"
            pool_address = Pubkey.from_string(self.pool_address)  # TODO: handle error

            self.dlmm = DLMM_CLIENT.create(
                pool_address, self.RPC, cluster
            )  # TODO: add local JS server PORT as param
        if dPK or dAddr or dRPC:
            self.dllmApp = MeteoraApp(self.client, self.user, self.dlmm, self.bin_num)

        self.prev_private_key = self.private_key
        self.prev_RPC = self.RPC
        self.prev_pool_address = self.pool_address

    def proceed_pool(self, claim_fee_now: bool):
        ret = self.dllmApp.get_positions_by_user_and_lb_pair()
        if ret is None:
            return True

        actibe_bin = ret.active_bin
        logger.info(
            "Active bin: %s, price: %s", actibe_bin.bin_id, actibe_bin.price_per_token
        )

        u_pos = ret.user_positions
        curr_price = float(actibe_bin.price_per_token)
        if not (self.TakeProfit > curr_price > self.StopLoss):
            logger.warning(
                "Price %s not in bounds (%s, %s)",
                curr_price,
                self.StopLoss,
                self.TakeProfit,
            )
            self.dllmApp.close_pool_swap_all(u_pos)
            return False

        for pos0 in u_pos:
            lower_bin_id = pos0.position_data.lower_bin_id
            upper_bin_id = pos0.position_data.upper_bin_id
            logger.debug(
                "Processing position %s: bins [%s, %s]",
                pos0.public_key,
                lower_bin_id,
                upper_bin_id,
            )

            if upper_bin_id >= actibe_bin.bin_id >= lower_bin_id:
                if claim_fee_now:
                    logger.info("Claiming fees for in-range position")
                    self.dllmApp.claim_pos_fee(pos0)
                else:
                    logger.info("Position in range, no action needed")
            else:
                XtoY = actibe_bin.bin_id < lower_bin_id
                logger.info(
                    "Position out of range, performing %s swap",
                    "XtoY" if XtoY else "YtoX",
                )
                self.dllmApp.close_pos_swap_half(pos0, actibe_bin.bin_id, XtoY)

        return True

    def run(self):
        claim_fee_now = False
        fee_time = time.time()

        while True:
            if self.claim_fee and time.time() - fee_time > self.sleep_time * 12:
                claim_fee_now = True
                logger.info("Scheduled fee claim")

            try:
                able_to_continue = self.proceed_pool(claim_fee_now)
                if not able_to_continue:
                    logger.info("Pool processing stopped")
                    break
            except Exception as e:
                logger.error("Error in pool processing: %s", str(e))

            if claim_fee_now:
                claim_fee_now = False
                fee_time = time.time()
                logger.debug("Reset fee claim timer")

            logger.info("Sleeping for %s seconds", self.sleep_time)
            time.sleep(self.sleep_time)
