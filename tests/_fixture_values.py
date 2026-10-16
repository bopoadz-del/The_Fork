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
