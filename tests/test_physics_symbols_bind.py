"""The model writes E and I, the calculators take ec_mpa and i_mm4.

Live on 9e6fe98, both cases answered with a tool error instead of a figure:

    T2   "Unknown argument(s) for beam_deflection_cantilever_udl: E, I.
          These were not applied. Expected: ... ec_mpa (MPa), i_mm4"
    T12  "Unknown argument(s) for modulus_of_elasticity_concrete: c"

Both had been passing. #740 made an unbound key a hard error rather than a
silent drop -- which is right, a silently dropped input is how a wrong figure
gets computed -- but it turned two long-standing binding gaps into visible
failures. `E` and `I` are what an engineer writes and what the model therefore
sends; `c` is the model shortening `code`.

Renaming is not enough, and this is the whole difficulty: `E` is quoted in
GPa (200) while ``ec_mpa`` wants MPa (200 000), and `I` is quoted in m4
(2.0e-4) while ``i_mm4`` wants mm4 (2.0e8). A rename alone would compute a
deflection a thousand times wrong with no error at all -- worse than the tool
error it replaced.

The unit is inferred from magnitude, and only where the two ranges cannot
overlap in engineering practice: a modulus of elasticity below 1000 is GPa
(concrete 20-40, steel 200; nothing real sits between 1000 and 20 000 MPa),
and a second moment of area below 1.0 is m4 (a 10 mm square bar is 833 mm4).
Outside those ranges the value is taken as already canonical, and the existing
m4 guard still refuses anything it cannot make sense of.
"""
import pytest

from app.lib import construction_formulas as cf

CANTILEVER = "beam_deflection_cantilever_udl"
MODULUS = "modulus_of_elasticity_concrete"


def _run(name, params):
    return cf.run_calculation(name, params)


def _value(name, params):
    out = _run(name, params)
    assert out["status"] == "success", out
    r = out["result"]
    return r["value"] if isinstance(r, dict) else r


# ── the two live failures ──────────────────────────────────────────────────

def test_t2_physics_symbols_bind_and_convert():
    # 10 kN/m over 3 m, E = 200 GPa, I = 2.0e-4 m4 -> 2.53 mm.
    assert _value(CANTILEVER, {"w_kn_m": 10, "span_m": 3,
                               "E": 200, "I": 2.0e-4}) == pytest.approx(2.53, abs=0.01)


def test_t12_shortened_code_binds():
    r = _run(MODULUS, {"fck_n_mm2": 35, "c": "aci"})
    assert r["status"] == "success", r
    assert r["result"]["value"] == pytest.approx(27806.0, abs=1.0)
    assert r["result"]["unit"] == "MPa"


def test_the_canonical_names_are_unchanged():
    assert _value(CANTILEVER, {"w_kn_m": 10, "span_m": 3,
                               "ec_mpa": 200_000, "i_mm4": 2.0e8}) == pytest.approx(2.53, abs=0.01)
    assert _run(MODULUS, {"fck_n_mm2": 35, "code": "aci"})["status"] == "success"


# ── the unit inference, which is where a wrong figure would come from ──────

def test_a_modulus_already_in_mpa_is_not_scaled_again():
    # E = 200000 is MPa already; scaling would give 2.53e-3 mm.
    assert _value(CANTILEVER, {"w_kn_m": 10, "span_m": 3,
                               "E": 200_000, "I": 2.0e8}) == pytest.approx(2.53, abs=0.01)


def test_both_spellings_agree():
    symbols = _value(CANTILEVER, {"w_kn_m": 10, "span_m": 3, "E": 200, "I": 2.0e-4})
    canonical = _value(CANTILEVER, {"w_kn_m": 10, "span_m": 3,
                                    "ec_mpa": 200_000, "i_mm4": 2.0e8})
    assert symbols == pytest.approx(canonical, abs=1e-9)


def test_a_canonical_key_wins_over_a_symbol():
    # If the model sends both, the explicit one is the instruction.
    out = _value(CANTILEVER, {"w_kn_m": 10, "span_m": 3,
                              "ec_mpa": 200_000, "i_mm4": 2.0e8, "E": 999, "I": 999})
    assert out == pytest.approx(2.53, abs=0.01)


def test_a_second_moment_that_is_neither_unit_is_still_refused():
    # 1e-30 converts to 1e-18 mm4 -- no section. The m4 guard must still fire.
    out = _run(CANTILEVER, {"w_kn_m": 10, "span_m": 3, "E": 200, "I": 1e-30})
    assert out["status"] != "success"


# ── an unknown key is still an error; this does not weaken that ────────────

def test_a_genuinely_unknown_argument_still_errors():
    out = _run(CANTILEVER, {"w_kn_m": 10, "span_m": 3,
                            "ec_mpa": 200_000, "i_mm4": 2.0e8, "bogus_param": 1})
    assert out["status"] != "success"
    assert "bogus_param" in str(out)


def test_symbols_do_not_leak_into_calculators_without_those_params():
    # `modulus_of_rupture` has no ec_mpa/i_mm4; E must not bind to anything.
    out = _run("modulus_of_rupture", {"fck_n_mm2": 30, "E": 200})
    assert out["status"] != "success"
    assert "E" in str(out.get("unknown", [])) or "E" in str(out)
