"""D7: the decision-artifact parser."""
import pytest

from agent import parse_artifact


@pytest.mark.parametrize("text, expected", [
    ('{"version":1,"value":47}', (47, True)),                         # exact
    ('{"version": 1, "value": 47}', (47, True)),                      # whitespace allowed
    ('The total is 47.\n{"version":1,"value":47}', (47, True)),       # extra text around it
    ('```json\n{"version":1,"value":47}\n```', (47, True)),           # code fence
    ('{"value":47,"version":1}', (47, False)),                        # reordered: valid, not exact
    ('{"version":1,"value":47} {"value":47,"version":1}', (47, False)),
    ('{"version":1,"value":47} again {"version":1,"value":47}', (47, True)),  # same value twice
    ('{"version":1,"value":-3}', (-3, True)),
])
def test_valid(text, expected):
    assert parse_artifact(text) == expected


@pytest.mark.parametrize("text", [
    '{"version":1,"value":<integer>}',                    # the task's placeholder
    '{"version":2,"value":47}',                           # wrong version
    '{"version":1,"value":47.0}',                         # decimal
    '{"value":47.0,"version":1}',
    '{"version":1,"value":"47"}',                         # string, not integer
    '{"version":1,"value":47} {"version":1,"value":52}',  # conflicting artifacts
    'The total is 47.',                                   # no artifact
    '',
    None,
])
def test_unscored(text):
    assert parse_artifact(text) == (None, False)
