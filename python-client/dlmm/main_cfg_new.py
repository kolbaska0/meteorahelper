import logging
import os
import time
from datetime import datetime
from typing import List, Optional, Tuple
from solana.rpc.api import Client
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import Transaction
from dlmm import DLMM_CLIENT, DLMM
from dlmm.types import StrategyParameters, StrategyType, Position
import solders.transaction_status
from solders.signature import Signature
from solana.rpc import types
import configparser
from solana.rpc import commitment
from solana.rpc.core import (
    TransactionExpiredBlockheightExceededError,
    UnconfirmedTxError,
    RPCException,
)


# Configure logging
def setup_logger():
    logger = logging.getLogger("SolanaTradingBot")
    logger.setLevel(logging.DEBUG)

    # File handler for persistent logs
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(
        f"%s/solana_trading_%s.log" % (log_dir, timestamp)
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


def load_keypair_from_base58(private_key: str) -> Optional[Keypair]:
    try:
        return Keypair.from_base58_string(private_key)
    except Exception as e:
        logger.error("Error loading keypair from base58: %s", str(e))
        raise


def load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config_file = "config.ini"

    if os.path.exists(config_file):
        logger.info("Loading configuration from %s", config_file)
        config.read(config_file)
    else:
        logger.info("Config file %s not found, creating new configuration", config_file)
        config["Settings"] = {}

    return config


def save_config(config: configparser.ConfigParser):
    try:
        with open("config.ini", "w") as configfile:
            config.write(configfile)
        logger.info("Configuration saved to config.ini")
    except Exception as e:
        logger.error("Error saving configuration: %s", str(e))


def get_sol_token_changes(user: Pubkey, sig: Signature, interested_token: Pubkey):
    pre_sol, post_sol, pre_token, post_token = None, None, None, None
    try:
        tx_info = client.get_transaction(sig).value
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

        if meta.pre_token_balances and meta.post_token_balances:
            for i, token in enumerate(meta.pre_token_balances):
                if token.mint == interested_token and token.owner == user:
                    pre_token = int(token.ui_token_amount.amount)
                    post_token = int(meta.post_token_balances[i].ui_token_amount.amount)
                    logger.info("Token balance change: %s -> %s", pre_token, post_token)
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
            if key == user:
                pre_sol = meta.pre_balances[i]
                post_sol = meta.post_balances[i]
                logger.info("SOL balance change: %s -> %s", pre_sol, post_sol)
                break

    except Exception as e:
        logger.error("Error in get_sol_token_changes for tx %s: %s", sig, str(e))

    return pre_sol, post_sol, pre_token, post_token


def get_sol(user: Pubkey) -> Optional[int]:
    try:
        balance = client.get_balance(user).value
        logger.debug("SOL balance for %s: %s", user, balance)
        return balance
    except Exception as e:
        logger.error("Error fetching SOL balance for %s: %s", user, str(e))
        return None


def get_tok(user: Pubkey, interested_token: Pubkey) -> Optional[int]:
    try:
        opts = types.TokenAccountOpts(mint=interested_token)
        token_accounts = client.get_token_accounts_by_owner(user, opts).value
        if not token_accounts:
            logger.warning(
                "No token accounts found for %s and token %s", user, interested_token
            )
            return None

        token_balance = client.get_token_account_balance(token_accounts[0].pubkey).value
        x_amount = int(token_balance.amount)
        logger.debug(
            "Token balance for %s, token %s: %s", user, interested_token, x_amount
        )
        return x_amount
    except Exception as e:
        logger.error(
            "Error fetching token balance for %s, token %s: %s",
            user,
            interested_token,
            str(e),
        )
        return None


def confirm_multi_tx(
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
            # NOTE: return value ignored
            client.confirm_transaction(
                tx_sig=tx,
                commitment=client.commitment,
                last_valid_block_height=last_valid_block_height,
                sleep_seconds=sleep_seconds,
            )
            confirmed.append(tx)
        except (TransactionExpiredBlockheightExceededError, UnconfirmedTxError) as e:
            print(e)
            expired.append(tx)
            # ? all next txs will be expired ?
        except (RPCException, Exception) as e:
            print(e)
            with_errors.append(tx)

    return confirmed, expired, with_errors


from functools import wraps
from typing import Callable


def multi_tx_decorator(
    resend_retries: int = 5,
    reconfirm_retries: int = 2,
    sleep_seconds: float = 0.5,
    hight: int = 150,
    partial_expiration_ok: bool = False,
):
    def decorator(create_tx_func: Callable):
        @wraps(create_tx_func)
        def wrapper(*args, **kwargs):
            confirmed_sigs = []
            for i in range(resend_retries):
                # CREATE TX (func, opts)
                created_txs = create_tx_func(*args, **kwargs)
                # [], None - err
                # [tx], tx - ok
                if not created_txs:
                    print("not created_txs")
                    continue
                if isinstance(created_txs, Transaction):
                    created_txs = [created_txs]

                # SEND TX (txs)
                txs = []
                for tx in created_txs:
                    tx_send = client.send_transaction(
                        tx
                    ).value  # FIXME: may fails here (Transaction simulation failed)
                    print(tx_send)
                    txs.append(tx_send)

                # CONFIRM TX (sent_txs)
                bh = client.get_block_height().value + hight
                expired_all = []
                with_errors = []
                for j in range(reconfirm_retries):
                    confirmed, expired, with_errors = confirm_multi_tx(
                        txs=txs,
                        last_valid_block_height=bh,
                        sleep_seconds=sleep_seconds,
                    )
                    confirmed_sigs += confirmed
                    expired_all += expired
                    if len(with_errors) == 0:
                        break
                    txs = with_errors

                if len(with_errors) != 0:
                    print("confirmation failed")
                    print(with_errors)
                    return False, confirmed_sigs

                if len(expired_all) == 0:
                    print("all confirmed")
                    return True, confirmed_sigs

                if len(expired_all) == len(created_txs):
                    print("all expired")
                else:
                    if partial_expiration_ok:
                        print("partial expiration ok")
                    else:
                        print("partial expiration not ok")
                        print(expired_all)
                        return False, confirmed_sigs
                print(expired_all)

            return False, confirmed_sigs

        return wrapper

    return decorator


@multi_tx_decorator(partial_expiration_ok=False)
def close_pos(user: Keypair, dlmm: DLMM, pos0: Position) -> List[Transaction]:
    """
    Ret:
        ok: [TX]
        err: []
    """
    try:
        txs = dlmm.remove_liqidity(
            position_pub_key=pos0.public_key,
            user=user.pubkey(),
            bin_ids=[pos0.position_data.lower_bin_id, pos0.position_data.upper_bin_id],
            bps=10000,
            should_claim_and_close=True,
        )
        for tx in txs:
            tx.sign([user], client.get_latest_blockhash().value.blockhash)
        logger.info(
            "Prepared %s transactions to close position %s", len(txs), pos0.public_key
        )
        return txs
    except Exception as e:
        logger.error(
            "Error preparing close position transaction for %s: %s",
            pos0.public_key,
            str(e),
        )
        return []


def swap_(
    user: Keypair, dlmm: DLMM, swap_amount: int, in_token: Pubkey, out_token: Pubkey
) -> Optional[Transaction]:
    "Private function"
    try:
        bin_arrays = dlmm.get_bin_array_for_swap(False)
        tx = dlmm.swap(
            in_token=in_token,
            out_token=out_token,
            in_amount=swap_amount,
            min_out_amount=0,
            lb_pair=dlmm.pool_address,
            user=user.pubkey(),
            binArrays=[arr["publicKey"] for arr in bin_arrays],
        )
        tx.sign([user], client.get_latest_blockhash().value.blockhash)
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


@multi_tx_decorator()
def swap_XY(user: Keypair, dlmm: DLMM, swap_amount: int) -> Optional[Transaction]:
    """
    Ret:
        ok: TX
        err: None
    """
    return swap_(
        user=user,
        dlmm=dlmm,
        swap_amount=swap_amount,
        in_token=dlmm.token_X.public_key,
        out_token=dlmm.token_Y.public_key,
    )


@multi_tx_decorator()
def swap_YX(user: Keypair, dlmm: DLMM, swap_amount: int) -> Optional[Transaction]:
    """
    Ret:
        ok: TX
        err: None
    """
    return swap_(
        user=user,
        dlmm=dlmm,
        swap_amount=swap_amount,
        in_token=dlmm.token_Y.public_key,
        out_token=dlmm.token_X.public_key,
    )


@multi_tx_decorator()
def open_pos_spot(
    user: Keypair,
    dlmm: DLMM,
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

        tx = dlmm.initialize_position_and_add_liquidity_by_strategy(
            position_pub_key=position_pub_key.pubkey(),
            user=user.pubkey(),
            x_amount=x_amount,
            y_amount=y_amount,
            strategy=StrategyParameters(
                min_bin_id=min_bin_id,
                max_bin_id=max_bin_id,
                strategy_type=StrategyType.SpotOneSide,
            ),
        )
        tx.sign([user, position_pub_key], client.get_latest_blockhash().value.blockhash)
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


SOL_ADDR = Pubkey.from_string("So11111111111111111111111111111111111111112")


def close_pool_swap_all(user: Keypair, dlmm: DLMM, u_pos: List[Position]):
    X = dlmm.token_X.public_key
    Y = dlmm.token_Y.public_key
    if X == SOL_ADDR:
        TOK = Y
        SOL_IS_Y = False
        get_x = lambda u_pub: get_sol(u_pub)
        get_y = lambda u_pub: get_tok(u_pub, TOK)
    elif Y == SOL_ADDR:
        TOK = X
        SOL_IS_Y = True
        get_y = lambda u_pub: get_sol(u_pub)
        get_x = lambda u_pub: get_tok(u_pub, TOK)
    else:
        logger.error("SOL token not found in pool")
        return

    start_sol = get_sol(user.pubkey())
    logger.info("Starting SOL balance: %s", start_sol)

    tok_amount = 0
    for pos in u_pos:
        amount_str = (
            pos.position_data.total_x_amount
            if SOL_IS_Y
            else pos.position_data.total_y_amount
        )
        ok, _ = close_pos(user, dlmm, pos)
        if not ok:
            logger.error("Failed to close position: %s", pos.public_key)
        else:
            tok_amount += int(amount_str)

    swap = swap_XY if SOL_IS_Y else swap_YX
    ok, _ = swap(user, dlmm, tok_amount)
    if not ok:
        logger.error("Failed to swap tokens")

    end_sol = get_sol(user.pubkey())
    logger.info(
        "End SOL balance: %s, Difference: %s",
        end_sol,
        end_sol - start_sol if start_sol is not None and end_sol is not None else "N/A",
    )


def close_pos_swap_half(
    user: Keypair,
    dlmm: DLMM,
    pos0: Position,
    active_bin_id: int,
    XtoY: bool,
    bin_num: int,
):
    X = dlmm.token_X.public_key
    Y = dlmm.token_Y.public_key
    if X == SOL_ADDR:
        TOK = Y
        SOL_IS_Y = False
        get_x = lambda u_pub: get_sol(u_pub)
        get_y = lambda u_pub: get_tok(u_pub, TOK)
    elif Y == SOL_ADDR:
        TOK = X
        SOL_IS_Y = True
        get_y = lambda u_pub: get_sol(u_pub)
        get_x = lambda u_pub: get_tok(u_pub, TOK)
    else:
        logger.error("SOL token not found in pool")
        return

    logger.info(
        "Starting balances - SOL: %s, TOK: %s",
        get_sol(user.pubkey()),
        get_tok(user.pubkey(), TOK),
    )

    src_amount = (
        int(pos0.position_data.total_x_amount)
        if XtoY
        else int(pos0.position_data.total_y_amount)
    )
    logger.info("%s, amount: %s", "Swap X to Y" if XtoY else "Swap Y to X", src_amount)

    ok, _ = close_pos(user, dlmm, pos0)
    if not ok:
        logger.error("Failed to close position: %s", pos0.public_key)
        return

    pre_dst_man_f = get_y if XtoY else get_x
    swap = swap_XY if XtoY else swap_YX
    pre_dst_man = pre_dst_man_f(user.pubkey())

    new_src_amount = src_amount // 2
    swap_amount = src_amount - new_src_amount
    ok, confirmed = swap(user, dlmm, swap_amount)
    if not ok:
        logger.error("Failed to swap tokens")
        return

    pre_sol, post_sol, pre_token, post_token = get_sol_token_changes(
        user.pubkey(), confirmed[0], TOK
    )

    if XtoY:
        if SOL_IS_Y:  # MEM -> SOL
            pre_dst = pre_sol
            post_dst = post_sol

            if pre_dst is None or post_dst is None:
                pre_dst = pre_dst_man  # get with get_sol so int too
                post_dst = get_sol(user.pubkey())
        else:  # SOL -> MEM
            pre_dst = pre_token
            post_dst = post_token

            if pre_dst is None or post_dst is None:
                pre_dst = pre_dst_man if pre_dst_man is not None else 0
                post_dst = get_tok(user.pubkey(), TOK)

        if post_dst is None or pre_dst is None:
            logger.error("Can't get DESTINATION token amount")
            return

        new_y_amount = post_dst - pre_dst
        new_x_amount = new_src_amount
    else:
        if not SOL_IS_Y:  # SOL <- MEM
            pre_dst = pre_sol
            post_dst = post_sol

            if pre_dst is None or post_dst is None:
                pre_dst = pre_dst_man  # get with get_sol so int too
                post_dst = get_sol(user.pubkey())
        else:  # MEM <- SOL
            pre_dst = pre_token
            post_dst = post_token

            if pre_dst is None or post_dst is None:
                pre_dst = pre_dst_man if pre_dst_man is not None else 0
                post_dst = get_tok(user.pubkey(), TOK)

        if post_dst is None or pre_dst is None:
            logger.error("Can't get DESTINATION token amount")
            return

        new_x_amount = post_dst - pre_dst
        new_y_amount = new_src_amount

    logger.info("New position amounts: x=%s, y=%s", new_x_amount, new_y_amount)

    ok, _ = open_pos_spot(
        user, dlmm, new_x_amount, new_y_amount, active_bin_id, bin_num
    )
    if not ok:
        logger.error("Failed to open position")

    logger.info(
        "Ending balances - SOL: %s, TOK: %s",
        get_sol(user.pubkey()),
        get_tok(user.pubkey(), TOK),
    )


def claim_pos_fee(user: Keypair, dlmm: DLMM, pos0: Position):
    try:
        txs = dlmm.claim_all_rewards(user.pubkey(), [pos0])
        for tx in txs:
            tx.sign([user], client.get_latest_blockhash().value.blockhash)
            tx_send = client.send_transaction(tx)
            logger.info(
                "Sent claim fee transaction for position %s: %s",
                pos0.public_key,
                tx_send.value,
            )
    except Exception as e:
        logger.error("Error claiming fees for position %s: %s", pos0.public_key, str(e))


def proceed_pool(
    user: Keypair,
    address: str,
    StopLoss: float,
    TakeProfit: float,
    claim_fee: bool = False,
    bin_num: int = 69,
) -> bool:
    try:
        pool_address = Pubkey.from_string(address)
    except ValueError:
        logger.error("Invalid pool address: %s", address)
        return False

    try:
        dlmm = DLMM_CLIENT.create(pool_address, RPC, cluster)
        ret = dlmm.get_positions_by_user_and_lb_pair(user.pubkey())
    except Exception as e:
        logger.error("Error initializing DLMM or fetching positions: %s", str(e))
        return True

    actibe_bin = ret.active_bin
    logger.info(
        "Active bin: %s, price: %s", actibe_bin.bin_id, actibe_bin.price_per_token
    )

    u_pos = ret.user_positions
    curr_price = float(actibe_bin.price_per_token)
    if not (TakeProfit > curr_price > StopLoss):
        logger.warning(
            "Price %s not in bounds (%s, %s)", curr_price, StopLoss, TakeProfit
        )
        close_pool_swap_all(user, dlmm, u_pos)
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
            if claim_fee:
                logger.info("Claiming fees for in-range position")
                claim_pos_fee(user, dlmm, pos0)
            else:
                logger.info("Position in range, no action needed")
        else:
            XtoY = actibe_bin.bin_id < lower_bin_id
            logger.info(
                "Position out of range, performing %s swap", "XtoY" if XtoY else "YtoX"
            )
            close_pos_swap_half(user, dlmm, pos0, actibe_bin.bin_id, XtoY, bin_num)

    return True


if __name__ == "__main__":
    logger.info("Starting Solana Trading Bot")

    config = load_config()
    settings = config["Settings"] if "Settings" in config else {}

    # Initialize defaults
    address = settings.get("address", "")
    StopLoss = settings.get("StopLoss", "")
    TakeProfit = settings.get("TakeProfit", "")
    bin_num = settings.get("bin_num", "")
    claim_fee = settings.get("claim_fee", "").lower()
    sleep_time = settings.get("sleep_time", "")
    PRIVATE_KEY = settings.get("PRIVATE_KEY", "")
    RPC = settings.get("RPC", "")
    cluster = settings.get("cluster", "")

    # Prompt for missing or invalid settings
    if not address:
        address = input("Enter pool address: ").strip()
        if not address:
            logger.error("Empty pool address provided")
            exit(1)
        settings["address"] = address

    if not StopLoss:
        StopLoss = input("Enter StopLoss: ").strip()
    try:
        StopLoss = float(StopLoss)
        settings["StopLoss"] = str(StopLoss)
    except ValueError:
        logger.error("StopLoss must be a float")
        exit(1)

    if not TakeProfit:
        TakeProfit = input("Enter TakeProfit: ").strip()
    try:
        TakeProfit = float(TakeProfit)
        settings["TakeProfit"] = str(TakeProfit)
    except ValueError:
        logger.error("TakeProfit must be a float")
        exit(1)

    if StopLoss >= TakeProfit:
        logger.error("StopLoss must be less than TakeProfit")
        exit(1)

    if not bin_num:
        bin_num = input("Enter bin_num (default 69): ").strip() or "69"
    try:
        bin_num = int(bin_num)
        settings["bin_num"] = str(bin_num)
    except ValueError:
        logger.error("bin_num must be an integer")
        exit(1)

    if not claim_fee:
        claim_fee_input = input("Claim fee? (y/n): ").strip().lower()
        if claim_fee_input not in ("y", "n"):
            logger.error("Invalid input for claim_fee, must be 'y' or 'n'")
            exit(1)
        claim_fee = claim_fee_input
    claim_fee = claim_fee == "y"
    settings["claim_fee"] = "y" if claim_fee else "n"

    if not sleep_time:
        sleep_time = (
            input("Enter sleep time in seconds (default 1800): ").strip() or "1800"
        )
    try:
        sleep_time = float(sleep_time)
        if sleep_time < 0:
            logger.error("sleep_time must be positive")
            exit(1)
        settings["sleep_time"] = str(sleep_time)
    except ValueError:
        logger.error("sleep_time must be a float")
        exit(1)

    if not PRIVATE_KEY:
        PRIVATE_KEY = input("Enter private base58 key: ").strip()
        if not PRIVATE_KEY:
            logger.error("Empty private key provided")
            exit(1)
        settings["PRIVATE_KEY"] = PRIVATE_KEY

    if not RPC:
        RPC = input("Enter RPC URL: ").strip()
        if not RPC:
            logger.error("Empty RPC URL provided")
            exit(1)
        settings["RPC"] = RPC

    if not cluster:
        cluster = (
            input("Enter cluster (default mainnet-beta): ").strip() or "mainnet-beta"
        )
        settings["cluster"] = cluster

    # Save updated config
    if not os.path.exists("config.ini"):
        config["Settings"] = settings
        save_config(config)

    # Initialize user and client
    try:
        user = load_keypair_from_base58(PRIVATE_KEY)
    except Exception as e:
        logger.critical("Failed to load keypair: %s", str(e))
        exit(1)

    if not user:
        logger.critical("Failed to load keypair: unknown error")
        exit(1)

    try:
        client = Client(RPC, commitment.Confirmed)
    except Exception as e:
        logger.critical("Failed to initialize client with RPC %s: %s", RPC, str(e))
        exit(1)

    claim_fee_now = False
    fee_time = time.time()

    while True:
        if claim_fee and time.time() - fee_time > sleep_time * 12:
            claim_fee_now = True
            logger.info("Scheduled fee claim")

        try:
            if not proceed_pool(
                user, address, StopLoss, TakeProfit, claim_fee_now, bin_num
            ):
                logger.info("Pool processing stopped")
                break
        except Exception as e:
            logger.error("Error in pool processing: %s", str(e))

        if claim_fee_now:
            claim_fee_now = False
            fee_time = time.time()
            logger.debug("Reset fee claim timer")

        logger.info("Sleeping for %s seconds", sleep_time)
        time.sleep(sleep_time)
