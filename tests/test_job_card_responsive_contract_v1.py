from __future__ import annotations

import inspect

from app import product_pages, product_ui


def test_prepare_action_has_wider_column_and_full_customer_label() -> None:
    source = inspect.getsource(product_pages._job_card)

    assert 'st.columns((0.8, 0.8, 1.6)' in source
    assert '"Prepare application"' in source
    assert '"Open original job"' in source
    assert "Apply via:" in source
    assert "Source:" in source


def test_job_card_css_wraps_long_values_and_stacks_primary_action_on_mobile() -> None:
    css = product_ui.product_css()

    assert ".job-destination" in css
    assert "white-space:normal!important" in css
    assert "st-key-product_actions_" in css
    assert ":last-child{flex-basis:100%!important}" in css
