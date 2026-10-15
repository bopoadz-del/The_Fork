"""Every calculation the model reads leads with a headline sentence.

Order 1310Z items 5 and 8 (lane 3 thermal shrinkage with no headline; lane 4
a raw 'Result:' line and the thermal check's 23 C differential row dropped):
the rendering the model wrote from had no headline and repeated the primary
figure's value / unit / label keys as if they were results. It now leads
with one labelled figure -- the result's own primary figure, else the
formula's first declared output that is not an input echoed back -- and the
mirror keys are gone from the Result line.
"""
from app.agents.driver import render
from app.lib import construction_formulas as cf


def _render(name, **p):
    return render.render_calculation(cf.run_calculation(name, p), p)


def test_a_primary_figure_is_the_headline_and_not_repeated_as_results():
    text = _render("concrete_thermal_cracking_check", core_temp_c=68, surface_temp_c=45)
    assert "Headline: Delta t c is 23 degC." in text
    result_line = next(line for line in text.splitlines() if line.startswith("Result:"))
    assert "label" not in result_line and "unit degC" not in result_line and "value 23" not in result_line


def test_without_a_primary_figure_the_first_real_output_is_the_headline():
    assert "Headline: Equivalent temp drop is 20 degC." in _render("thermal_shrinkage_equivalence")


def test_the_headline_label_does_not_repeat_its_unit():
    assert render.headline(cf.run_calculation("excavation_volume", {"length_m": 10, "width_m": 8, "depth_m": 3})) \
        == "Headline: Bank volume is 240 m3."


def test_a_declared_main_output_is_the_headline():
    """LANE3 on #973: the fallback took the first numeric output -- a cost
    rate with a volume / mass / area unit -- on concrete, rebar, formwork and
    mobilisation. A formula now declares the output its answer leads with,
    and its rate outputs carry their real units."""
    cases = {
        "cost_buildup_concrete": ({"quantity_m3": 100}, "Headline: Selling price is 308.83 currency/m3."),
        "cost_buildup_rebar": ({"quantity_t": 20}, "Headline: Selling price is 4,856 currency/t."),
        "mobilization_cost_estimate": ({"num_personnel": 50, "duration_months": 6}, "Headline: Grand total is 2,627,775 currency."),
    }
    for name, (params, expected) in cases.items():
        assert render.headline(cf.run_calculation(name, params)) == expected, name


def test_a_main_output_must_be_a_declared_output():
    import pytest
    from app.lib.formula_registry import formula

    with pytest.raises(ValueError):
        formula(owner="base", description="d", display_name="Qx", inputs={}, outputs={"a": "m"}, main_output="b")
