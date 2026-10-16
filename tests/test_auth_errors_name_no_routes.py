"""A user-facing auth error never names an API route.

Order 0010Z item 3 (LANE2 sign-up root cause): the unverified-login 403
told a person to "request a new one at /v1/users/resend-verification" -- a
path, not something a user can do. Every HTTPException text in the auth
router is read from the source, so a new one is covered too.
"""
import ast
from pathlib import Path

AUTH_ROUTER = Path(__file__).resolve().parents[1] / "app" / "routers" / "users.py"


def _error_texts():
    tree = ast.parse(AUTH_ROUTER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "HTTPException":
            for arg in list(node.args) + [k.value for k in node.keywords]:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        yield node.lineno, sub.value


def test_no_auth_error_names_an_api_route():
    leaks = [(line, text) for line, text in _error_texts() if "/v1/" in text]
    assert not leaks, leaks


def test_the_unverified_message_points_at_the_sign_in_page_control():
    texts = " ".join(t for _, t in _error_texts())
    assert "Resend confirmation email" in texts
