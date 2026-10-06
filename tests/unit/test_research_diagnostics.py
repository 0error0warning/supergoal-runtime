import pytest

from experiments.public_benchmarks.research_diagnostics import artifact_files, criteria_check, validate_grade


def test_rubric_must_preserve_actual_user_provenance():
    with pytest.raises(ValueError):
        criteria_check({'criteria': [{'id': 'C1', 'description': 'Add a dashboard',
                                      'source_quote': 'Build a dashboard'}]}, 'Summarize this report')


def test_unobserved_report_credit_is_not_a_valid_grade():
    report = 'This report contains an unsupported numerical assertion.'
    criteria = [{'id': 'C1'}]
    data = {'criteria': [{'id': 'C1', 'score': 1, 'reason': 'looks complete', 'report_quote': 'invented evidence'}],
            'sampled_claims': [{'report_quote': report, 'verdict': 'unverifiable'}]}
    with pytest.raises(ValueError, match='exact report quote'):
        validate_grade(data, criteria, report, {})
    data['criteria'][0]['report_quote'] = report
    result = validate_grade(data, criteria, report, {})
    assert result['sampled_claim_counts'] == {'supported': 0, 'contradicted': 0, 'unverifiable': 1}


def test_claim_support_requires_real_source_observation():
    report = 'Collection rates improved by fifteen percent.'
    data = {'criteria': [{'id': 'C1', 'score': 1, 'reason': 'answered', 'report_quote': report}],
            'sampled_claims': [{'report_quote': report, 'verdict': 'supported', 'source_id': 'D1',
                                'source_quote': 'fifteen percent improvement'}]}
    with pytest.raises(ValueError, match='real source quote'):
        validate_grade(data, [{'id': 'C1'}], report, {'D1': 'Different content'})


def test_agent_export_size_is_bounded_before_hashing(tmp_path):
    (tmp_path / 'large').write_bytes(b'12345')
    with pytest.raises(ValueError, match='size limits'):
        artifact_files(tmp_path, max_bytes=4)


def test_agent_export_cannot_follow_host_secret_symlink(tmp_path):
    export = tmp_path / 'export'
    export.mkdir()
    secret = tmp_path / 'host-only'
    secret.write_text('must not be read')
    try:
        (export / 'input').symlink_to(secret)
    except OSError:
        pytest.skip('Creating test symlinks requires platform privileges')
    with pytest.raises(ValueError, match='symlink'):
        artifact_files(export)
