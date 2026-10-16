"""Values the auth tests type in: generated per run, never written in the source."""
import secrets

PASSWORD = "pw-" + secrets.token_hex(8)
OTHER_PASSWORD = "pw-" + secrets.token_hex(8)
WRONG_PASSWORD = "pw-" + secrets.token_hex(8)
RESEND_TEST_KEY = "re_" + secrets.token_hex(12)
