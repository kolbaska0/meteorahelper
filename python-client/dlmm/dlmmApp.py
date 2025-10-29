import logging
import os
from datetime import datetime
from typing import List, Optional, Tuple
from solana.rpc.api import Client
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import Transaction
from dlmm import DLMM
from dlmm.types import (
    StrategyParameters,
    StrategyType,
    Position,
    DlmmHttpError as HTTPError,
)
import solders.transaction_status
from solders.signature import Signature
from solana.rpc import types
from solana.rpc.core import (
    TransactionExpiredBlockheightExceededError,
    UnconfirmedTxError,
    RPCException,
)
from const import LOG_NAME

from functools import wraps
from typing import Callable
import time


def setup_logger():
    logger = logging.getLogger("LiquiDMon_BCK")
    logger.setLevel(logging.DEBUG)

    # File handler for persistent logs
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(f"{log_dir}/{LOG_NAME}_BCK_{timestamp}.log")
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

SOL_ADDR = Pubkey.from_string("So11111111111111111111111111111111111111112")


class MeteoraApp:
    def __init__(
        self,
        client: Client,
        user: Keypair,
        dlmm: DLMM,
        bin_num: int,
        slippage: float,
        strategy: StrategyType,
    ):
        self.client = client  # have same rpc
        self.user = user
        self.dlmm = dlmm

        self.strategy = strategy
        self.bin_num = bin_num
        self.slippage = int(slippage * 100)

        self.X = self.dlmm.token_X.public_key
        self.Y = self.dlmm.token_Y.public_key
        if self.X == SOL_ADDR:
            self.TOK = self.Y
            self.SOL_IS_Y = False
            self.get_x = self.get_sol
            self.get_y = self._get_tok
        else:
            self.TOK = self.X
            self.SOL_IS_Y = True
            self.get_y = self.get_sol
            self.get_x = self._get_tok
            if self.Y != SOL_ADDR:
                logger.error(
                    "SOL token not found in pool, but found %s and %s", self.X, self.Y
                )
                raise ValueError("SOL token not found in pool")

    def get_positions_by_user_and_lb_pair(self):
        num = 3
        for i in range(num):
            try:
                return self.dlmm.get_positions_by_user_and_lb_pair(
                    self.user.pubkey()
                )  # OK
            except HTTPError as e:
                logger.error("(%s) HTTPError: %s", i, e)
                # raise e # Server not responding
                break
            except Exception as e:
                logger.error("(%s) Exception: %s", i, e)
                # if i == num - 1:
                #     raise e # Wrong arguments
                time.sleep(0.1)
                # Some timeouts, try again
        return None

    def get_balance_almost(self):
        sol = self.get_sol()
        if sol is None:
            return None

        ret = self.get_positions_by_user_and_lb_pair()
        if ret is None:
            return None

        tok_amount = 0
        sol_amount = 0
        tok_unclaimed = 0
        sol_unclaimed = 0
        tok_claimed = 0
        sol_claimed = 0
        for pos in ret.user_positions:
            if self.SOL_IS_Y:
                sol_amount += int(pos.position_data.total_y_amount)
                tok_amount += int(pos.position_data.total_x_amount)
                sol_unclaimed += int(pos.position_data.fee_Y, 16)
                tok_unclaimed += int(pos.position_data.fee_X, 16)
                sol_claimed += int(pos.position_data.total_claimed_fee_Y_amount, 16)
                tok_claimed += int(pos.position_data.total_claimed_fee_X_amount, 16)
            else:
                sol_amount += int(pos.position_data.total_x_amount)
                tok_amount += int(pos.position_data.total_y_amount)
                sol_unclaimed += int(pos.position_data.fee_X, 16)
                tok_unclaimed += int(pos.position_data.fee_Y, 16)
                sol_claimed += int(pos.position_data.total_claimed_fee_X_amount, 16)
                tok_claimed += int(pos.position_data.total_claimed_fee_Y_amount, 16)

        tok_price = ret.active_bin.price
        convert_coeff = tok_price if self.SOL_IS_Y else 1 / tok_price

        tok_amount_sol = int(tok_amount * convert_coeff)
        tok_unclaimed_sol = int(tok_unclaimed * convert_coeff)
        tok_claimed_sol = int(tok_claimed * convert_coeff)

        amount_sol = sol_amount + tok_amount_sol
        unclaimed_sol = sol_unclaimed + tok_unclaimed_sol
        claimed_sol = sol_claimed + tok_claimed_sol

        ret = sol + amount_sol
        logger.info(
            "sol=%s, sol_amount=%s, tok_amount=%s, tok_price=%s, tok_amount_sol=%s, ret=%s, claimed_sol=%s, unclaimed_sol=%s",
            sol,
            sol_amount,
            tok_amount,
            tok_price,
            tok_amount_sol,
            ret,
            claimed_sol,
            unclaimed_sol,
        )
        return ret, amount_sol, claimed_sol, unclaimed_sol

    def claim_pos_fee(self, pos0: Position, need_to_update: bool = False):
        if not need_to_update:
            ok, confirmed = self._claim_pos_fee(pos0)
            if not ok:
                logger.error("Failed to claim fees for position: %s", pos0.public_key)
            return

        x_amount_man = self.get_x()
        y_amount_man = self.get_y()
        logger.info("Starting balances - X: %s, Y: %s", x_amount_man, y_amount_man)

        ok, confirmed = self._claim_pos_fee(pos0)
        if not ok:
            logger.error("Failed to claim fees for position: %s", pos0.public_key)
            return
        pre_sol, post_sol, pre_token, post_token = self._get_sol_token_changes(
            confirmed[0]
        )

        if self.SOL_IS_Y:
            if pre_sol is None or post_sol is None:
                pre_sol = y_amount_man
                post_sol = self.get_sol()
            if pre_token is None or post_token is None:
                pre_token = x_amount_man
                post_token = self._get_tok()
            if (
                pre_sol is None
                or post_sol is None
                or pre_token is None
                or post_token is None
            ):
                logger.error("Failed to get pre and post balances")
                return

            to_add_y = post_sol - pre_sol
            to_add_x = post_token - pre_token
        else:
            if pre_sol is None or post_sol is None:
                pre_sol = x_amount_man
                post_sol = self.get_sol()
            if pre_token is None or post_token is None:
                pre_token = y_amount_man
                post_token = self._get_tok()
            if (
                pre_sol is None
                or post_sol is None
                or pre_token is None
                or post_token is None
            ):
                logger.error("Failed to get pre and post balances")
                return

            to_add_x = post_sol - pre_sol
            to_add_y = post_token - pre_token

        to_add_x = max(to_add_x, 0)
        to_add_y = max(to_add_y, 0)

        logger.info("to add X: %s, to add Y: %s", to_add_x, to_add_y)
        if self.dlmm.token_X.decimal and self.dlmm.token_Y.decimal:
            x_pw = pow(10, self.dlmm.token_X.decimal - 6)
            y_pw = pow(10, self.dlmm.token_Y.decimal - 6)

            if to_add_x < x_pw and to_add_y < y_pw:
                logger.info("Fee is too small, skipping claim fees")
                # TODO: maybe accumulate fees?
                return

        ok, _ = self._add_liquidity(pos0, to_add_x, to_add_y)
        if not ok:
            logger.error("Failed to add liquidity")

    def close_pool_swap_all(self, poses: list[Position]):
        start_sol = self.get_sol()
        logger.info("Starting SOL balance: %s", start_sol)

        tok_amount = 0
        for pos in poses:
            amount_str = (
                pos.position_data.total_x_amount
                if self.SOL_IS_Y
                else pos.position_data.total_y_amount
            )
            ok, _ = self._close_pos(pos)
            if not ok:
                logger.error("Failed to close position: %s", pos.public_key)
            else:
                tok_amount += int(amount_str)

        swap = self._swap_XY if self.SOL_IS_Y else self._swap_YX
        ok, _ = swap(tok_amount)
        if not ok:
            logger.error("Failed to swap tokens")

        end_sol = self.get_sol()
        logger.info("End SOL balance: %s", end_sol)

    def close_pos_swap_half(self, pos0: Position, active_bin_id: int, XtoY: bool):
        logger.info(
            "Starting balances - SOL: %s, TOK: %s",
            self.get_sol(),
            self._get_tok(),
        )

        src_amount = (
            int(pos0.position_data.total_x_amount)
            if XtoY
            else int(pos0.position_data.total_y_amount)
        )
        logger.info(
            "%s, amount: %s", "Swap X to Y" if XtoY else "Swap Y to X", src_amount
        )

        ok, _ = self._close_pos(pos0)
        if not ok:
            logger.error("Failed to close position: %s", pos0.public_key)
            return

        pre_dst_man_f = self.get_y if XtoY else self.get_x
        swap = self._swap_XY if XtoY else self._swap_YX
        pre_dst_man = pre_dst_man_f()

        new_src_amount = src_amount // 2
        swap_amount = src_amount - new_src_amount
        ok, confirmed = swap(swap_amount)
        if not ok:
            logger.error("Failed to swap tokens")
            return

        pre_sol, post_sol, pre_token, post_token = self._get_sol_token_changes(
            confirmed[0]
        )

        if XtoY:
            if self.SOL_IS_Y:  # MEM -> SOL
                pre_dst = pre_sol
                post_dst = post_sol

                if pre_dst is None or post_dst is None:
                    pre_dst = pre_dst_man  # get with get_sol so int too
                    post_dst = self.get_sol()
            else:  # SOL -> MEM
                pre_dst = pre_token
                post_dst = post_token

                if pre_dst is None or post_dst is None:
                    pre_dst = pre_dst_man if pre_dst_man is not None else 0
                    post_dst = self._get_tok()

            if post_dst is None or pre_dst is None:
                logger.error("Can't get DESTINATION token amount")
                return

            new_y_amount = post_dst - pre_dst
            new_x_amount = new_src_amount
        else:
            if not self.SOL_IS_Y:  # SOL <- MEM
                pre_dst = pre_sol
                post_dst = post_sol

                if pre_dst is None or post_dst is None:
                    pre_dst = pre_dst_man  # get with get_sol so int too
                    post_dst = self.get_sol()
            else:  # MEM <- SOL
                pre_dst = pre_token
                post_dst = post_token

                if pre_dst is None or post_dst is None:
                    pre_dst = pre_dst_man if pre_dst_man is not None else 0
                    post_dst = self._get_tok()

            if post_dst is None or pre_dst is None:
                logger.error("Can't get DESTINATION token amount")
                return

            new_x_amount = post_dst - pre_dst
            new_y_amount = new_src_amount

        logger.info("New position amounts: x=%s, y=%s", new_x_amount, new_y_amount)

        ok, _ = self._open_pos(new_x_amount, new_y_amount, active_bin_id, self.bin_num)
        if not ok:
            logger.error("Failed to open position")

        logger.info(
            "Ending balances - SOL: %s, TOK: %s",
            self.get_sol(),
            self._get_tok(),
        )

    def get_sol(self) -> Optional[int]:
        try:
            pubkey = self.user.pubkey()
            balance = self.client.get_balance(pubkey).value
            logger.debug("SOL balance for %s: %s", pubkey, balance)
            return balance
        except Exception as e:
            logger.error("Error fetching SOL balance for %s: %s", pubkey, str(e))
            return None

    def _get_tok(self) -> Optional[int]:
        try:
            pubkey = self.user.pubkey()
            opts = types.TokenAccountOpts(mint=self.TOK)
            token_accounts = self.client.get_token_accounts_by_owner(pubkey, opts).value
            if not token_accounts:
                logger.warning(
                    "No token accounts found for %s and token %s",
                    pubkey,
                    self.TOK,
                )
                return None

            token_balance = self.client.get_token_account_balance(
                token_accounts[0].pubkey
            ).value
            x_amount = int(token_balance.amount)
            logger.debug(
                "Token balance for %s, token %s: %s", pubkey, self.TOK, x_amount
            )
            return x_amount
        except Exception as e:
            logger.error(
                "Error fetching token balance for %s, token %s: %s",
                pubkey,
                self.TOK,
                str(e),
            )
            return None

    def _get_sol_token_changes(self, sig: Signature):
        pre_sol, post_sol, pre_token, post_token = None, None, None, None
        interested_token = self.TOK
        try:
            tx_info = self.client.get_transaction(sig).value
            if not tx_info:
                logger.warning("Transaction info not found for signature: %s", sig)
                return pre_sol, post_sol, pre_token, post_token

            meta = tx_info.transaction.meta
            if not meta:
                logger.warning("Transaction meta not found for signature: %s", sig)
                return pre_sol, post_sol, pre_token, post_token

            if meta.err:
                logger.warning("Transaction error: %s", meta.err)
                # return pre_sol, post_sol, pre_token, post_token

            # logger.debug("Transaction logs: %s", meta.log_messages)

            pubkey = self.user.pubkey()
            if meta.pre_token_balances and meta.post_token_balances:
                for i, token in enumerate(meta.pre_token_balances):
                    if token.mint == interested_token and token.owner == pubkey:
                        pre_token = int(token.ui_token_amount.amount)
                        post_token = int(
                            meta.post_token_balances[i].ui_token_amount.amount
                        )
                        logger.info(
                            "Token balance change: %s -> %s", pre_token, post_token
                        )
                        break

            transaction = tx_info.transaction.transaction
            if not transaction:
                logger.warning("Transaction data not found for signature: %s", sig)
                return pre_sol, post_sol, pre_token, post_token

            if isinstance(transaction, solders.transaction_status.UiAccountsList):
                account_keys = transaction.account_keys
            else:
                account_keys = transaction.message.account_keys

            for i, key in enumerate(account_keys):
                if key == pubkey:
                    pre_sol = meta.pre_balances[i]
                    post_sol = meta.post_balances[i]
                    logger.info("SOL balance change: %s -> %s", pre_sol, post_sol)
                    break

        except Exception as e:
            logger.error("Error in get_sol_token_changes for tx %s: %s", sig, str(e))

        return pre_sol, post_sol, pre_token, post_token

    def _confirm_multi_tx(
        self,
        txs: list[Signature],
        last_valid_block_height: int | None = None,
        sleep_seconds: float = 0.5,
    ):
        confirmed = []
        expired = []
        with_errors = []
        # ? is this really valid ?
        for tx in txs:
            try:
                ret = self.client.confirm_transaction(
                    tx_sig=tx,
                    commitment=self.client.commitment,
                    last_valid_block_height=last_valid_block_height,
                    sleep_seconds=sleep_seconds,
                ).value
                if not ret or not ret[0]:
                    continue  # UNREACHABLE
                res = ret[0]
                if res.err:
                    raise Exception(
                        f"{res.err=}, status: {res.status=}.\nAdditional info on solscan"
                    )

                confirmed.append(tx)
            except (
                TransactionExpiredBlockheightExceededError,
                UnconfirmedTxError,
            ) as e:
                logger.warning("Transaction expired: %s: %s", tx, str(e))
                expired.append(tx)
                # ? all next txs will be expired ?
            except (RPCException, Exception) as e:
                logger.warning("Transaction error: %s: %s", tx, str(e))
                with_errors.append(tx)

        return confirmed, expired, with_errors

    @staticmethod
    def _multi_tx_decorator(
        resend_retries: int = 5,
        reconfirm_retries: int = 2,
        sleep_seconds: float = 0.5,
        hight: int = 150,
        partial_expiration_ok: bool = False,
    ):
        def decorator(create_tx_func: Callable):
            @wraps(create_tx_func)
            def wrapper(self, *args, **kwargs) -> Tuple[bool, List[Signature]]:
                confirmed_sigs: List[Signature] = []
                logger.debug(
                    "Starting transaction processing for %s",
                    create_tx_func.__name__,
                )

                for attempt in range(resend_retries):
                    logger.info(
                        "Transaction attempt %d of %d", attempt + 1, resend_retries
                    )

                    # Create transactions
                    try:
                        created_txs = create_tx_func(self, *args, **kwargs)
                        # [], None - err
                        # [tx], tx - ok
                        if not created_txs:
                            logger.warning(
                                "No transactions created on attempt %d", attempt + 1
                            )
                            continue
                        if isinstance(created_txs, Transaction):
                            created_txs = [created_txs]

                        logger.debug("Created %d transactions", len(created_txs))
                    except Exception as e:
                        logger.error("Failed to create transactions: %s", str(e))
                        continue

                    # Send transactions
                    tx_sigs: List[Signature] = []
                    for tx in created_txs:
                        try:
                            tx_send = self.client.send_transaction(tx).value
                            tx_sigs.append(tx_send)
                            logger.info("Sent transaction: %s", tx_send)
                        except Exception as e:
                            logger.error("Failed to send transaction: %s", str(e))
                            continue  # TODO: rethink

                    # Confirm transactions
                    try:
                        block_height = self.client.get_block_height().value + hight
                        logger.debug(
                            "Using block height %d for confirmation", block_height
                        )
                    except Exception as e:
                        logger.error("Failed to get block height: %s", str(e))
                        continue

                    expired_all: List[Signature] = []
                    with_errors: List[Signature] = []
                    for confirm_attempt in range(reconfirm_retries):
                        logger.debug(
                            "Confirmation attempt %d of %d",
                            confirm_attempt + 1,
                            reconfirm_retries,
                        )
                        confirmed, expired, with_errors = self._confirm_multi_tx(
                            txs=tx_sigs,
                            last_valid_block_height=block_height,
                            sleep_seconds=sleep_seconds,
                        )
                        confirmed_sigs.extend(confirmed)
                        expired_all.extend(expired)

                        logger.info(
                            "Confirmation results: %d confirmed, %d expired, %d with errors",
                            len(confirmed),
                            len(expired),
                            len(with_errors),
                        )

                        if not with_errors:
                            break
                        tx_sigs = with_errors
                        logger.debug(
                            "Retrying confirmation for %d transactions",
                            len(with_errors),
                        )

                    if with_errors:
                        logger.error(
                            "Confirmation failed for %d transactions", len(with_errors)
                        )
                        return False, confirmed_sigs

                    if not expired_all:
                        logger.info("All transactions confirmed successfully")
                        return True, confirmed_sigs

                    if len(expired_all) == len(created_txs):
                        logger.warning(
                            "All transactions expired on attempt %d", attempt + 1
                        )
                    else:
                        if partial_expiration_ok:
                            logger.info("Partial expiration occurred but allowed")
                            return True, confirmed_sigs
                        else:
                            logger.error(
                                "Partial expiration not allowed: %d transactions expired",
                                len(expired_all),
                            )
                            return False, confirmed_sigs

                logger.error("All retry attempts failed")
                return False, confirmed_sigs

            return wrapper

        return decorator

    @_multi_tx_decorator(partial_expiration_ok=False)
    def _close_pos(self, pos0: Position) -> List[Transaction]:
        """
        Ret:
            ok: [TX]
            err: []
        """
        try:
            txs = self.dlmm.remove_liqidity(
                position_pub_key=pos0.public_key,
                user=self.user.pubkey(),
                bin_ids=[
                    pos0.position_data.lower_bin_id,
                    pos0.position_data.upper_bin_id,
                ],
                bps=10000,
                should_claim_and_close=True,
            )
            for tx in txs:
                tx.sign([self.user], self.client.get_latest_blockhash().value.blockhash)
            logger.info(
                "Prepared %s transactions to close position %s",
                len(txs),
                pos0.public_key,
            )
            return txs
        except Exception as e:
            logger.error(
                "Error preparing close position transaction for %s: %s",
                pos0.public_key,
                str(e),
            )
            return []

    @_multi_tx_decorator(partial_expiration_ok=False)
    def _claim_pos_fee(self, pos0: Position):
        try:
            txs = self.dlmm.claim_all_rewards(self.user.pubkey(), [pos0])
            for tx in txs:
                tx.sign([self.user], self.client.get_latest_blockhash().value.blockhash)
                tx_send = self.client.send_transaction(tx)
                logger.info(
                    "Sent claim fee transaction for position %s: %s",
                    pos0.public_key,
                    tx_send.value,
                )
            logger.info(
                "Prepared %s transactions to claim fees for position %s",
                len(txs),
                pos0.public_key,
            )
            return txs
        except Exception as e:
            logger.error(
                "Error preparing claim fee transaction for position %s: %s",
                pos0.public_key,
                str(e),
            )
            return []

    def _swap_(
        self, swap_amount: int, in_token: Pubkey, out_token: Pubkey, swap_Y_to_X: bool
    ) -> Optional[Transaction]:
        "Private function"
        try:
            bin_arrays = self.dlmm.get_bin_array_for_swap(swap_Y_to_X)
            swap_quote = self.dlmm.swap_quote(
                swap_amount, swap_Y_to_X, self.slippage, bin_arrays
            )

            tx = self.dlmm.swap(
                in_token=in_token,
                out_token=out_token,
                in_amount=swap_amount,
                min_out_amount=0,  # swap_quote.min_out_amount, # TODO: fix swap_quote.min_out_amount
                lb_pair=self.dlmm.pool_address,
                user=self.user.pubkey(),
                binArrays=swap_quote.bin_arrays_pubkey,
            )
            tx.sign([self.user], self.client.get_latest_blockhash().value.blockhash)
            logger.info(
                "Prepared swap transaction: amount=%s, in_token=%s, out_token=%s",
                swap_amount,
                in_token,
                out_token,
            )
            return tx
        except Exception as e:
            logger.error("Error preparing swap transaction: %s", str(e))
            return None

    @_multi_tx_decorator()
    def _swap_XY(self, swap_amount: int) -> Optional[Transaction]:
        """
        Ret:
            ok: TX
            err: None
        """
        return self._swap_(
            swap_amount=swap_amount,
            in_token=self.dlmm.token_X.public_key,
            out_token=self.dlmm.token_Y.public_key,
            swap_Y_to_X=False,
        )

    @_multi_tx_decorator()
    def _swap_YX(self, swap_amount: int) -> Optional[Transaction]:
        """
        Ret:
            ok: TX
            err: None
        """
        return self._swap_(
            swap_amount=swap_amount,
            in_token=self.dlmm.token_Y.public_key,
            out_token=self.dlmm.token_X.public_key,
            swap_Y_to_X=True,
        )

    @_multi_tx_decorator()
    def _open_pos(
        self,
        x_amount: int,
        y_amount: int,
        active_bin_id: int,
        bins_around: int,
    ) -> Optional[Transaction]:
        """
        Ret:
            ok: TX
            err: None
        """
        try:
            position_pub_key = Keypair()
            minus_bins = (bins_around - 1) // 2
            plus_bins = (bins_around - 1) - minus_bins
            min_bin_id = active_bin_id - minus_bins
            max_bin_id = active_bin_id + plus_bins

            tx = self.dlmm.initialize_position_and_add_liquidity_by_strategy(
                position_pub_key=position_pub_key.pubkey(),
                user=self.user.pubkey(),
                x_amount=x_amount,
                y_amount=y_amount,
                strategy=StrategyParameters(
                    min_bin_id=min_bin_id,
                    max_bin_id=max_bin_id,
                    strategy_type=self.strategy,
                ),
            )
            tx.sign(
                [self.user, position_pub_key],
                self.client.get_latest_blockhash().value.blockhash,
            )
            logger.info(
                "Prepared open position transaction: x_amount=%s, y_amount=%s, bin_id=%s",
                x_amount,
                y_amount,
                active_bin_id,
            )
            return tx
        except Exception as e:
            logger.error("Error preparing open position transaction: %s", str(e))
            return None

    @_multi_tx_decorator()
    def _add_liquidity(
        self,
        pos0: Position,
        x_amount: int,
        y_amount: int,
    ) -> Optional[Transaction]:
        """
        Ret:
            ok: TX
            err: None
        """
        try:
            tx = self.dlmm.add_liquidity_by_strategy(
                pos0.public_key,
                self.user.pubkey(),
                x_amount,
                y_amount,
                StrategyParameters(
                    min_bin_id=pos0.position_data.lower_bin_id,
                    max_bin_id=pos0.position_data.upper_bin_id,
                    strategy_type=self.strategy,
                ),
            )
            tx.sign(
                [self.user],  # TODO: check if pos0.public_key is needed
                self.client.get_latest_blockhash().value.blockhash,
            )
            logger.info(
                "Prepared add liquidity transaction: pos=%s, x_amount=%s, y_amount=%s",
                pos0.public_key,
                x_amount,
                y_amount,
            )
            return tx
        except Exception as e:
            logger.error("Error preparing add liquidity transaction: %s", str(e))
            return None
