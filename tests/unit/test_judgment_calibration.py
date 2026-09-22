"""Historical calibration evidence cannot authorize a generation-four run."""
import json
import subprocess
import sys

import pytest

from tests.unit.test_judgment_evaluation import evaluation


@pytest.mark.parametrize('operation', ['calibrate', 'verify', 'verify-report'])
def test_incomplete_generation_four_calibration_refuses_before_provider_calls(tmp_path, operation):
    plan, artifact = tmp_path / 'plan.json', tmp_path / 'artifact.json'
    plan.write_text(evaluation.encoded(evaluation.freeze(1)))
    historical = json.dumps(dict(schema_version=3, quality_accepted=True, source='historical'))
    artifact.write_text(historical)
    result = subprocess.run([sys.executable, 'scripts/judgment_evaluation.py', operation,
        '--plan', str(plan), '--artifact', str(artifact)], capture_output=True, text=True)
    assert result.returncode == 2 and 'complete population and owner manifest' in result.stderr
    assert artifact.read_text() == historical and 'QUALITY PASS' not in result.stdout


@pytest.mark.parametrize('population, error', [('calibration-v3', 'Invalid comparison plan'), ('judgment-acceptance-v3', 'Invalid comparison plan')])
def test_old_population_cannot_freeze_current_calibration_or_acceptance(population, error):
    with pytest.raises(ValueError, match=error):
        evaluation.freeze(1, name=population)


def test_old_pair_manifest_cannot_supply_current_owner_labels():
    with pytest.raises(ValueError, match='not complete'):
        evaluation.freeze(1, pairs={'pairs': [{'owner_label': 'pass'}]})
