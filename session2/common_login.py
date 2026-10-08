"""
common_login.py  -  TWO login helpers, one per environment:

    get_prod_client()   PROD  -> market DATA only (historical candles + the live stream).
                                 Read-only in this workshop. Never used to place an order.
    get_client()        UAT   -> the sandbox, where every ORDER goes (fake money).

Every other file in session2/ does:

    from common_login import get_prod_client, get_client
    data   = get_prod_client()       # candles + WebSocket
    orders = get_client()            # only when you want to place a sandbox order (--trade)

and then reuses those objects for instruments, market data and trading
(the docs say: initialise once, reuse everywhere).

WHY TWO ENVIRONMENTS
    UAT holds very little price history (some stocks have one candle), so a 20-day
    z-score cannot be computed there. PROD has the full history and the live stream.
    Orders stay in UAT so a bug costs nothing. Same SDK, same code, one word differs.

WHERE THE PROD LOGIN LIVES  (two options, picked automatically)
    a) presenter's machine: ../data/ already has a PROD session (auth_data.db*) and .env,
       made by ../data/auth.py. We reuse ../data/common.py's connect().
    b) everyone else:       session2/prod/.env  (PHONE_NO + MPIN of your PROD login) and the
       SDK's token file session2/prod/auth_data.db*. First run asks for an OTP in the terminal.
    Either way the UAT token stays in session2/ and the PROD token in its own folder, because
    the SDK keeps one token file per working directory and the two environments must not mix.

----------------------------------------------------------------------------------
WHAT HAPPENS WHEN YOU CALL get_client()           (facts from the SDK docs + source)
----------------------------------------------------------------------------------
1. The SDK stores its login token in a small file called  auth_data.db*  in the
   CURRENT WORKING DIRECTORY.  We chdir() into session2/ first, so the token always
   lives at  session2/auth_data.db*  (and never mixes with data/'s PROD token).
2. The SDK reads  PHONE_NO  and  MPIN  from a  .env  file in the working directory
   when you pass  env_creds=True  (docs: "Using .env Variables").
   We look for  session2/.env  first, then  data/.env  as a fallback.
3. On EVERY start the SDK re-verifies the saved token with your MPIN and gets a fresh
   session token.  So the MPIN is needed every run (from .env, or typed in).
4. First run ever (no auth_data.db yet):  the SDK sends an OTP to your phone and asks
   you to type it.  That MUST be done in a real terminal.  After that the token is
   saved and later runs are silent (as long as MPIN is in .env).

We NEVER print, log or store OTP / MPIN / tokens ourselves.  Only the SDK handles them.

.env format (copy .env.example -> .env):
    PHONE_NO="0000000000"
    MPIN="0000"
"""
import glob
import os
import sys

# ---------------------------------------------------------------- folder locations
HERE = os.path.dirname(os.path.abspath(__file__))              # .../session2
DATA_DIR = os.path.join(os.path.dirname(HERE), "data")         # .../data (older kit)
ENV_HERE = os.path.join(HERE, ".env")
ENV_DATA = os.path.join(DATA_DIR, ".env")
SESSION_GLOB = os.path.join(HERE, "auth_data.db*")
PROD_DIR = os.path.join(HERE, "prod")                          # .../session2/prod  (PROD .env + token)
DATA_COMMON = os.path.join(DATA_DIR, "common.py")              # presenter's PROD helper

# The SDK prints emoji ("Login successful" with a tick). On Windows consoles that use
# the old cp1252 code page this can crash a print(). Switch stdout/stderr to UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def find_env_file():
    """Return session2/.env if it exists, else None.
    data/.env is deliberately NOT used: it holds the PROD MPIN, and UAT has its own MPIN."""
    if os.path.exists(ENV_HERE):
        return ENV_HERE
    return None


def has_saved_session() -> bool:
    """True if the SDK already saved a UAT token in session2/ (auth_data.db*)."""
    return bool(glob.glob(SESSION_GLOB))


def _env_has(key: str, path: str) -> bool:
    """Does the .env file define KEY=... with a non-empty value? (never prints the value)"""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key) and "=" in line:
                    value = line.split("=", 1)[1].strip().strip("\"'")
                    return bool(value)
    except OSError:
        pass
    return False


def get_client():
    """
    Log in to Nubra UAT and return the authenticated `nubra` client.

    Exits with a plain-English message instead of hanging when it cannot work
    (for example: no .env and no terminal to type the OTP/MPIN into).
    """
    # 1) Work from session2/ so the SDK's token file and .env lookups land here.
    os.chdir(HERE)

    env_file = find_env_file()

    # 2) Safety: the SDK uses input() for anything missing. If this is not a real
    #    terminal (e.g. an automated run) that would crash, so stop early and explain.
    interactive = sys.stdin.isatty()
    if env_file is None and not interactive:
        sys.exit(
            "No .env found (looked for session2/.env and data/.env) and this is not an "
            "interactive terminal, so the SDK cannot ask for phone/OTP/MPIN.\n"
            "Fix: copy session2/.env.example to session2/.env and fill PHONE_NO and MPIN, "
            "then run once in a terminal to type the OTP."
        )
    if env_file is not None and not _env_has("MPIN", env_file) and not interactive:
        sys.exit(f"MPIN is missing in {env_file} and this is not an interactive terminal.")

    # 3) If the .env lives in data/ (not here), load it into the process environment
    #    ourselves. The SDK only auto-reads <cwd>/.env, but it falls back to
    #    os.getenv(), so this keeps env_creds=True working with the docs' key names.
    if env_file is not None and env_file != ENV_HERE:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=True)

    if not has_saved_session():
        print("No saved UAT session in session2/ yet: the SDK will send an OTP to your phone "
              "and ask for it (type it in this terminal; nothing is stored by our code).")

    # 4) The documented call. UAT = sandbox. env_creds=True = read PHONE_NO/MPIN from .env.
    from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv
    nubra = InitNubraSdk(NubraEnv.UAT, env_creds=(env_file is not None))

    # 5) Prove it worked without printing anything secret.
    if not nubra.token_data.get("session_token"):
        sys.exit("Login did not complete (no session token). Check phone/OTP/MPIN and retry.\n"
                 "If you saw 'EOF when reading a line' above, this window cannot take typed input: "
                 "run from a real terminal, and put PHONE_NO/MPIN in session2/.env.")
    print("Logged in to Nubra UAT (sandbox). Session token saved by the SDK in session2/.")
    return nubra


def get_prod_client():
    """
    Log in to Nubra PROD for market DATA (candles + live stream) and return the client.
    This client is never handed to NubraTrader in this kit: orders go through get_client() (UAT).

    Option a) ../data/ has a PROD session already (presenter): reuse ../data/common.py.
    Option b) session2/prod/: your own PROD .env + token, kept apart from the UAT ones.
    """
    from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

    # a) presenter's machine: a PROD token + .env already live in ../data
    if os.path.exists(DATA_COMMON) and glob.glob(os.path.join(DATA_DIR, "auth_data.db*")):
        import importlib.util
        spec = importlib.util.spec_from_file_location("prod_common", DATA_COMMON)
        prod_common = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(prod_common)     # chdir()s into ../data (the SDK reads cwd/.env)
            nubra = prod_common.connect()            # InitNubraSdk(NubraEnv.PROD, env_creds=...)
        finally:
            os.chdir(HERE)                           # back to session2/ for everything else
        print("Logged in to Nubra PROD for market data (session reused from ../data). Read-only today.")
        return nubra

    # b) everyone else: session2/prod/.env + session2/prod/auth_data.db*
    os.makedirs(PROD_DIR, exist_ok=True)
    env_file = os.path.join(PROD_DIR, ".env")
    interactive = sys.stdin.isatty()
    if not os.path.exists(env_file) and not interactive:
        sys.exit("No session2/prod/.env (PHONE_NO + MPIN of your PROD login) and this is not an "
                 "interactive terminal, so the SDK cannot ask for phone/OTP/MPIN.\n"
                 "Fix: copy .env.example to session2/prod/.env, fill it, then run once in a terminal "
                 "to type the OTP. (No PROD login at all? Use  python screener.py --offline)")
    if os.path.exists(env_file) and not _env_has("MPIN", env_file) and not interactive:
        sys.exit(f"MPIN is missing in {env_file} and this is not an interactive terminal.")
    if not glob.glob(os.path.join(PROD_DIR, "auth_data.db*")):
        print("No saved PROD session in session2/prod/ yet: the SDK will send an OTP to your phone "
              "and ask for it (type it in this terminal; nothing is stored by our code).")
    os.chdir(PROD_DIR)                               # token file + .env lookups land in prod/
    try:
        nubra = InitNubraSdk(NubraEnv.PROD, env_creds=os.path.exists(env_file))
    finally:
        os.chdir(HERE)
    if not nubra.token_data.get("session_token"):
        sys.exit("PROD login did not complete (no session token). Check phone/OTP/MPIN and retry.")
    print("Logged in to Nubra PROD for market data (token saved by the SDK in session2/prod/). Read-only today.")
    return nubra


if __name__ == "__main__":
    # Running this file directly = a login smoke test (PROD for data, then UAT for orders).
    import argparse
    ap = argparse.ArgumentParser(description="login smoke test")
    ap.add_argument("--prod-only", action="store_true", help="test only the PROD data login")
    ap.add_argument("--uat-only", action="store_true", help="test only the UAT sandbox login")
    a = ap.parse_args()
    from nubra_python_sdk.refdata.instruments import InstrumentData
    if not a.uat_only:
        prod = get_prod_client()
        n = len(InstrumentData(prod).get_instruments_dataframe(exchange="NSE"))
        print(f"PROD instruments master (NSE): {n} rows. Market data is ready.")
    if not a.prod_only:
        client = get_client()
        n = len(InstrumentData(client).get_instruments_dataframe(exchange="NSE"))
        print(f"UAT instruments master (NSE): {n} rows. Sandbox orders are ready. You are set for Session 2.")
