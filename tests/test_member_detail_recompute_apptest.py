"""D6-Recompute-Verifikation (Phase A.5).

Weist nach, dass eine Score-Gewicht-Aenderung in der Sidebar den ``trade_score``
in dem DataFrame aendert, der an ``render_member_detail`` uebergeben wird — also
dass D6 (Score -> Return) live auf Sidebar-Gewichte reagiert.

Strategie: AppTest mit einer schlanken Inline-App, die den EXAKTEN Dialog-Daten-
pfad aus ``streamlit_app._show_member_dialog`` reproduziert (number_input fuer
ein Gewicht -> ``_diff_weights`` -> ``recompute_subset`` -> Ergebnis-Score in
session_state). Die volle ``streamlit_app.py`` per AppTest zu fahren scheitert an
der teuren Datenpipeline (trades_master-Load); der hier isolierte Pfad ist
deterministisch und deckt die D6-relevante Kausalkette ab.

Ergaenzt wird das durch ``test_score_recompute.py`` (recompute_subset-Pfad pur).
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402


_INLINE_APP = textwrap.dedent(
    f"""
    import sys
    sys.path.insert(0, {str(ROOT)!r})
    import pandas as pd
    import streamlit as st
    from app.score_recompute import recompute_subset
    from config import settings as _settings

    # _diff_weights-Logik aus streamlit_app (nur Abweichungen vom Default).
    def _diff_weights(current, defaults):
        return {{k: v for k, v in current.items()
                if float(v) != float(defaults.get(k, 0))}}

    base_df = pd.DataFrame([{{
        'signals_triggered': 'committee',
        'ex_officio_role_keys': '',
        'trade_amount_max_usd': 0,
        'trade_score': -1,
        'trade_score_breakdown': '',
        'trade_warnings': '',
    }}])

    default_w = int(_settings.SCORING_WEIGHTS['committee'])
    w = st.number_input('committee-Gewicht', value=default_w, key='w_committee')

    weights = dict(_settings.SCORING_WEIGHTS)
    weights['committee'] = w
    override = _diff_weights(weights, _settings.SCORING_WEIGHTS)

    # Exakt der Dialog-Pfad: recompute mit weights_override (oder None).
    df = recompute_subset(base_df, excluded=[],
                          weights_override=override or None)
    st.session_state['member_score'] = int(df.iloc[0]['trade_score'])
    st.metric('trade_score', df.iloc[0]['trade_score'])
    """
)


def test_sidebar_weight_change_alters_member_trade_score():
    at = AppTest.from_string(_INLINE_APP)
    at.run()
    base_score = at.session_state['member_score']
    # Default-Lauf: committee-Gewicht == Settings-Default.
    from config import settings as _settings
    assert base_score == int(_settings.SCORING_WEIGHTS['committee'])

    # Gewicht aendern -> Recompute -> Score MUSS sich aendern.
    new_w = base_score + 33
    at.number_input(key='w_committee').set_value(new_w).run()
    changed_score = at.session_state['member_score']
    assert changed_score == new_w
    assert changed_score != base_score
