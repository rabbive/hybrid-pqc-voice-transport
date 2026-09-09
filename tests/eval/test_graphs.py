import os
from hpqv.eval import graphs


def test_writes_png(tmp_path):
    rows = [{"name":"hybrid","loss_pct":l,"mos":4.3-0.05*l,"frag_count":0,
             "ttfb_ms":40,"delay_ms":50} for l in (0,10,20,30)] + \
           [{"name":"naive","loss_pct":l,"mos":3.5-0.05*l,"frag_count":3,
             "ttfb_ms":80,"delay_ms":50} for l in (0,10,20,30)]
    p = graphs.plot_loss_vs_mos(rows, str(tmp_path/"mos.png"))
    assert os.path.getsize(p) > 0
