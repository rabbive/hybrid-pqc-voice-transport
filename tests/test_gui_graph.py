"""Graph scaling is pure: rows -> canvas coordinates. The Canvas only draws them."""
import pytest

from hpqv.gui_graph import MOS_HI, MOS_LO, WINDOW_S, Box, nice_max, scale_series, x_of

BOX = Box(left=50, top=10, right=650, bottom=210)    # 600 x 200 plot area


def row(t, loss=0.0, residual=0.0, recovered=0, mos=4.4):
    return {"t_s": t, "interval_loss_pct": loss, "residual_loss_pct": residual,
            "interval_recovered": recovered, "mos": mos}


def test_empty_rows_give_empty_series_and_the_floor_scale():
    series, y_max = scale_series([], BOX, now=0.0)
    assert y_max == 10.0
    assert set(series) == {"Loss %", "Residual %", "Recovered /s", "MOS"}
    assert all(pts == [] for pts in series.values())


def test_one_row_is_one_point_per_series():
    series, y_max = scale_series([row(5, loss=10, mos=1.0)], BOX, now=5.0)
    assert all(len(pts) == 1 for pts in series.values())
    x, y = series["Loss %"][0]
    assert x == pytest.approx(50 + 5 / WINDOW_S * 600)
    assert y_max == 10.0 and y == pytest.approx(10)                 # 10 % of a 10 scale = top
    assert series["MOS"][0][1] == pytest.approx(210)                # MOS 1.0 = bottom


def test_mos_axis_runs_one_to_four_and_a_half():
    series, _ = scale_series([row(1, mos=MOS_HI), row(2, mos=MOS_LO), row(3, mos=2.75)], BOX, now=3.0)
    ys = [y for _, y in series["MOS"]]
    assert ys == [pytest.approx(10), pytest.approx(210), pytest.approx(110)]


def test_left_axis_autoscales_to_the_largest_series_value():
    series, y_max = scale_series([row(1, loss=20, residual=5, recovered=45)], BOX, now=1.0)
    assert y_max == 50.0                                            # recovered/s 45 sets the scale
    assert series["Recovered /s"][0][1] == pytest.approx(10 + 200 * (1 - 45 / 50))
    assert series["Residual %"][0][1] == pytest.approx(10 + 200 * (1 - 5 / 50))


def test_window_fills_rightward_then_scrolls():
    assert x_of(0, BOX, now=10) == 50                                # still at the left edge
    assert x_of(10, BOX, now=10) == pytest.approx(50 + 10 / 60 * 600)
    assert x_of(120, BOX, now=120) == 650                            # newest sample at the right edge
    assert x_of(60, BOX, now=120) == 50                              # 60 s ago at the left edge


def test_rows_older_than_the_window_are_dropped():
    rows = [row(30), row(50), row(100)]
    series, _ = scale_series(rows, BOX, now=100.0)
    assert [round(x) for x, _ in series["MOS"]] == [round(x_of(50, BOX, 100)), 650]


def test_values_above_the_scale_clamp_to_the_plot_edge():
    series, _ = scale_series([row(1, mos=9.0)], BOX, now=1.0)
    assert series["MOS"][0][1] == pytest.approx(10)


@pytest.mark.parametrize("v,want", [(0, 10), (7, 10), (11, 20), (20, 20), (21, 50), (50, 50), (51, 100), (400, 100)])
def test_nice_max(v, want):
    assert nice_max(v) == want
