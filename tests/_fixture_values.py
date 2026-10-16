"""Values the auth tests type in: generated per run, never written in the source."""
import secrets

PASSWORD = "pw-" + secrets.token_hex(8)
OTHER_PASSWORD = "pw-" + secrets.token_hex(8)
WRONG_PASSWORD = "pw-" + secrets.token_hex(8)
RESEND_TEST_KEY = "re_" + secrets.token_hex(12)
WEAK_PASSWORD = secrets.token_hex(2)  # four characters: under the 8-character minimum


def _same_length(n: int) -> str:
    import string

    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(n))


# One per fixed password the tests used to write, generated with its length
# (a weak one stays short, distinct ones stay distinct).
PW_01 = _same_length(24)
PW_02 = _same_length(19)
PW_03 = _same_length(16)
PW_04 = _same_length(11)
PW_05 = _same_length(11)
PW_06 = _same_length(10)
PW_07 = _same_length(9)
PW_08 = _same_length(9)
PW_09 = _same_length(8)
PW_10 = _same_length(8)
PW_11 = _same_length(3)
PW_12 = _same_length(3)
PW_13 = _same_length(2)

# One per stub key/token the tests used to write, generated per run.
STUB_KEY_01 = "stub-" + secrets.token_hex(8)
# The nightly harness key carries the placeholder marker its guard test reads.
STUB_KEY_02 = "stub-not-a-real-key-" + secrets.token_hex(8)
STUB_KEY_03 = "stub-" + secrets.token_hex(8)
STUB_KEY_04 = "stub-" + secrets.token_hex(8)
STUB_KEY_05 = "stub-" + secrets.token_hex(8)
STUB_KEY_06 = "stub-" + secrets.token_hex(8)
STUB_KEY_07 = "stub-" + secrets.token_hex(8)
STUB_KEY_08 = "stub-" + secrets.token_hex(8)
STUB_KEY_09 = "stub-" + secrets.token_hex(8)
STUB_KEY_10 = "stub-" + secrets.token_hex(8)
STUB_KEY_11 = "stub-" + secrets.token_hex(8)
STUB_KEY_12 = "stub-" + secrets.token_hex(8)

# Stub provider keys and tokens, generated per run; each keeps the leading
# word the tests used (ds-, or-, synthetic-, ...) so provider detection is unchanged.
STUB_TOKEN_01 = "ds-" + secrets.token_hex(8)
STUB_TOKEN_02 = "test-" + secrets.token_hex(8)
STUB_TOKEN_03 = "or-" + secrets.token_hex(8)
STUB_TOKEN_04 = "test-" + secrets.token_hex(8)
STUB_TOKEN_05 = "fake-" + secrets.token_hex(8)
STUB_TOKEN_06 = "ds-" + secrets.token_hex(8)
STUB_TOKEN_07 = "fake-" + secrets.token_hex(8)
STUB_TOKEN_08 = "or-" + secrets.token_hex(8)
STUB_TOKEN_09 = "synthetic-" + secrets.token_hex(8)
STUB_TOKEN_10 = "ds-" + secrets.token_hex(8)
STUB_TOKEN_11 = "or-" + secrets.token_hex(8)
STUB_TOKEN_12 = "ds-" + secrets.token_hex(8)
STUB_TOKEN_13 = "fake-" + secrets.token_hex(8)
STUB_TOKEN_14 = "or-" + secrets.token_hex(8)
STUB_TOKEN_15 = "synthetic-" + secrets.token_hex(8)
STUB_TOKEN_16 = "test-" + secrets.token_hex(8)

# A bearer token for tests that only need some Authorization header.
BEARER_TOKEN = "stub-" + secrets.token_hex(12)

# API-key-shaped bearer values (not JWTs) and deliberately malformed tokens,
# generated per run.
API_KEY_A = "cb_live_" + secrets.token_hex(12)
API_KEY_B = "cb_live_" + secrets.token_hex(12)
API_KEY_C = "cb_live_" + secrets.token_hex(12)
GARBAGE_TOKEN = "garbage." + secrets.token_hex(6) + ".value"
