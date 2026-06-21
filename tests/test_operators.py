"""Operator data integrity — they ship as data and are referenced by id everywhere."""
from descartes.operators import OPERATOR_IDS, OPERATOR_TEXT, OPERATORS

EXPECTED = {
    "assumption", "falsify", "inversion", "named_source", "edge_case", "quantify",
    "root_cause", "reversibility", "second_order", "define", "deletion",
}


def test_all_eleven_operators_present():
    assert {o["id"] for o in OPERATORS} == EXPECTED
    assert len(OPERATORS) == 11


def test_ids_are_unique_and_have_prompts():
    assert len(OPERATOR_IDS) == len(set(OPERATOR_IDS))
    for o in OPERATORS:
        assert o["prompt"].strip()
        assert o["id"] == o["id"].lower()


def test_operator_text_lists_every_id():
    for oid in OPERATOR_IDS:
        assert oid in OPERATOR_TEXT
