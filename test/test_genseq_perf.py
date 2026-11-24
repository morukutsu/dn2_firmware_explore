"""Instruction budget reporting must expose spikes and exclude incomplete frames."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from emu_genseq_perf import AUDIO, FRAME_BUDGET, distribution, frame_budget, peak


def sample(pc, at):
    return {'pc': hex(pc), 'icount': at}


def test_budget_reports_overruns_instead_of_mean_only():
    rows = [sample(AUDIO[1], 10), sample(AUDIO[0], 100), sample(AUDIO[1], 50100),
            sample(AUDIO[0], 88100), sample(AUDIO[1], 178100),
            sample(AUDIO[0], 180100)]
    stats = frame_budget(rows)
    assert stats['samples'] == 2
    assert stats['mean'] == 70000
    assert stats['over_slot'] == 1
    assert stats['peak_margin'] == FRAME_BUDGET - 90000
    assert stats['p99'] == stats['max'] == 90000


def test_scheduler_cost_subtracts_interrupted_audio_span():
    rows = [sample(1, 100), sample(2, 100100)]
    assert peak(rows, (1, 2), [(20000, 80000)]) == 40000


def test_small_sample_percentiles_and_empty_input():
    stats = distribution([5, 1, 2, 3])
    assert stats['p50'] == 2 and stats['p95'] == stats['p99'] == 5
    with pytest.raises(ValueError, match='no timing samples'):
        distribution([])
