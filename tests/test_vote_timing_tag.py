"""Tests fuer tags/vote_timing_tag.py — VoteTimingTag."""

import json
import textwrap

import pandas as pd
import pytest

from tags.vote_timing_tag import VoteTimingTag


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def patched_tag(tmp_path, monkeypatch):
    """Patcht BILL_ACTIONS_PATH, BILL_SUBJECTS_PATH, SUBJECT_MAPPING_PATH auf tmp_path."""
    import tags.vote_timing_tag as vt

    bill_actions = tmp_path / 'bill_actions.json'
    bill_subjects = tmp_path / 'bill_subjects.json'
    mapping = tmp_path / 'subject_mapping.csv'

    monkeypatch.setattr(vt, 'BILL_ACTIONS_PATH', str(bill_actions))
    monkeypatch.setattr(vt, 'BILL_SUBJECTS_PATH', str(bill_subjects))
    monkeypatch.setattr(vt, 'SUBJECT_MAPPING_PATH', str(mapping))

    return {'bill_actions': bill_actions, 'bill_subjects': bill_subjects, 'mapping': mapping}


def write_minimal_fixtures(patched_tag, *,
                            bill_id='hr-119-7960',
                            vote_date='2025-03-15T12:00:00Z',
                            chamber='House',
                            subjects=None,
                            sector='Healthcare',
                            konfidenz='Hoch'):
    """Schreibt minimale, valide JSON/CSV-Fixtures in tmp_path."""
    if subjects is None:
        subjects = ['Public health']

    patched_tag['bill_actions'].write_text(json.dumps([{
        'bill_id': bill_id,
        'title': 'Test Bill',
        'policy_area': 'Health',
        'actions': [{'roll_call': [{'chamber': chamber, 'date': vote_date}]}],
    }]), encoding='utf-8')

    patched_tag['bill_subjects'].write_text(json.dumps([{
        'bill_id': bill_id,
        'legislative_subjects': subjects,
    }]), encoding='utf-8')

    patched_tag['mapping'].write_text(
        'legislative_subject,frequency,yf_sector,yf_industry,sector_rank,konfidenz,region,kommentar\n'
        f'Public health,100,{sector},Drug Manufacturers,1,{konfidenz},,\n',
        encoding='utf-8',
    )


def make_trade(**overrides) -> pd.DataFrame:
    """Erzeugt ein einzeiliges Trade-DataFrame mit sinnvollen Defaults."""
    base = {
        'trade_id': 'T001',
        'transaction_date': '2025-03-01',
        'Relevant_Sectors': json.dumps([{'sector': 'Healthcare'}]),
        'asset_class_excluded': False,
    }
    base.update(overrides)
    return pd.DataFrame([base])


# ---------------------------------------------------------------------------
# TestLoadSubjectMapping
# ---------------------------------------------------------------------------

class TestLoadSubjectMapping:

    def test_subject_mapping_aggregiert_sektoren_pro_subject(self, patched_tag):
        patched_tag['mapping'].write_text(
            'legislative_subject,frequency,yf_sector,yf_industry,sector_rank,konfidenz,region,kommentar\n'
            'Energy policy,100,Energy,Oil & Gas,1,Hoch,,\n'
            'Energy policy,80,Utilities,Utilities-Regulated Electric,2,Mittel,,\n',
            encoding='utf-8',
        )
        tag = VoteTimingTag()
        mapping = tag._load_subject_mapping()
        assert 'Energy policy' in mapping
        assert mapping['Energy policy']['sectors'] == {'Energy', 'Utilities'}

    def test_subject_mapping_behaelt_hoechste_konfidenz(self, patched_tag):
        patched_tag['mapping'].write_text(
            'legislative_subject,frequency,yf_sector,yf_industry,sector_rank,konfidenz,region,kommentar\n'
            'Tax reform,100,Financials,Banks,1,Niedrig,,\n'
            'Tax reform,80,Financials,Banks,2,Hoch,,\n',
            encoding='utf-8',
        )
        tag = VoteTimingTag()
        mapping = tag._load_subject_mapping()
        assert mapping['Tax reform']['konfidenz'] == 'Hoch'

    def test_subject_mapping_filtert_leere_yf_sector(self, patched_tag):
        patched_tag['mapping'].write_text(
            'legislative_subject,frequency,yf_sector,yf_industry,sector_rank,konfidenz,region,kommentar\n'
            'Unknown topic,100,,None,1,Niedrig,,\n'
            'Clean energy,50,Energy,Solar,1,Mittel,,\n',
            encoding='utf-8',
        )
        tag = VoteTimingTag()
        mapping = tag._load_subject_mapping()
        assert 'Unknown topic' not in mapping
        assert 'Clean energy' in mapping


# ---------------------------------------------------------------------------
# TestLoadVotes
# ---------------------------------------------------------------------------

class TestLoadVotes:

    def test_load_votes_extrahiert_roll_calls_aus_bill_actions(self, patched_tag):
        write_minimal_fixtures(patched_tag)
        tag = VoteTimingTag()
        df = tag.load_votes()

        assert len(df) == 1
        assert df.iloc[0]['chamber'] == 'House'
        assert df.iloc[0]['vote_date'].date() == pd.Timestamp('2025-03-15').date()

    def test_load_votes_dedup_per_bill_chamber_date(self, patched_tag):
        """Gleicher (bill_id, chamber, date) zweimal → nur 1 Zeile."""
        patched_tag['bill_actions'].write_text(json.dumps([{
            'bill_id': 'hr-119-1',
            'title': 'Dup Bill',
            'policy_area': 'Health',
            'actions': [
                {'roll_call': [{'chamber': 'House', 'date': '2025-03-15T12:00:00Z'}]},
                {'roll_call': [{'chamber': 'House', 'date': '2025-03-15T12:00:00Z'}]},
            ],
        }]), encoding='utf-8')
        patched_tag['bill_subjects'].write_text(json.dumps([{
            'bill_id': 'hr-119-1',
            'legislative_subjects': ['Public health'],
        }]), encoding='utf-8')
        patched_tag['mapping'].write_text(
            'legislative_subject,frequency,yf_sector,yf_industry,sector_rank,konfidenz,region,kommentar\n'
            'Public health,100,Healthcare,Drug Manufacturers,1,Hoch,,\n',
            encoding='utf-8',
        )
        tag = VoteTimingTag()
        df = tag.load_votes()
        assert len(df) == 1

    def test_load_votes_skippt_bills_ohne_gemappte_subjects(self, patched_tag):
        """Bill mit Subject das nicht im Mapping ist → kein Vote im Output."""
        patched_tag['bill_actions'].write_text(json.dumps([{
            'bill_id': 'hr-119-2',
            'title': 'Unknown Bill',
            'policy_area': 'Other',
            'actions': [{'roll_call': [{'chamber': 'Senate', 'date': '2025-04-01T00:00:00Z'}]}],
        }]), encoding='utf-8')
        patched_tag['bill_subjects'].write_text(json.dumps([{
            'bill_id': 'hr-119-2',
            'legislative_subjects': ['Completely unknown subject'],
        }]), encoding='utf-8')
        patched_tag['mapping'].write_text(
            'legislative_subject,frequency,yf_sector,yf_industry,sector_rank,konfidenz,region,kommentar\n'
            'Public health,100,Healthcare,Drug Manufacturers,1,Hoch,,\n',
            encoding='utf-8',
        )
        tag = VoteTimingTag()
        df = tag.load_votes()
        assert df.empty


# ---------------------------------------------------------------------------
# TestApply
# ---------------------------------------------------------------------------

class TestApply:

    def test_apply_findet_hit_im_zeitfenster(self, patched_tag):
        """Trade 2025-03-01, Vote 2025-03-15 (+14d) → Hit."""
        write_minimal_fixtures(patched_tag, vote_date='2025-03-15T12:00:00Z')
        tag = VoteTimingTag()
        result = tag.apply(make_trade(transaction_date='2025-03-01'))
        assert bool(result.iloc[0]['Vote_Timing_Hit']) is True
        assert result.iloc[0]['Vote_Timing_Hit_Count'] == 1

    def test_apply_kein_hit_ausserhalb_zeitfenster(self, patched_tag):
        """Trade 2025-03-01, Vote 2025-04-15 (+45d, jenseits 30d) → kein Hit."""
        write_minimal_fixtures(patched_tag, vote_date='2025-04-15T12:00:00Z')
        tag = VoteTimingTag()
        result = tag.apply(make_trade(transaction_date='2025-03-01'))
        assert bool(result.iloc[0]['Vote_Timing_Hit']) is False
        assert result.iloc[0]['Vote_Timing_Hit_Count'] == 0

    def test_apply_kein_hit_ohne_sektor_match(self, patched_tag):
        """Vote im Fenster, aber Sektor-Mismatch → kein Hit."""
        write_minimal_fixtures(patched_tag, vote_date='2025-03-15T12:00:00Z', sector='Energy')
        tag = VoteTimingTag()
        # Trade hat Healthcare-Sektoren, Vote-Sektor ist Energy
        result = tag.apply(make_trade(
            transaction_date='2025-03-01',
            Relevant_Sectors=json.dumps([{'sector': 'Healthcare'}]),
        ))
        assert bool(result.iloc[0]['Vote_Timing_Hit']) is False

    def test_apply_respektiert_asset_class_excluded(self, patched_tag):
        """asset_class_excluded=True → Trade wird uebersprungen, kein Hit."""
        write_minimal_fixtures(patched_tag, vote_date='2025-03-15T12:00:00Z')
        tag = VoteTimingTag()
        result = tag.apply(make_trade(transaction_date='2025-03-01', asset_class_excluded=True))
        assert bool(result.iloc[0]['Vote_Timing_Hit']) is False

    def test_apply_multiple_hits_sammelt_alle(self, patched_tag):
        """3 Votes im Fenster mit Sektor-Match → Hit_Count=3, JSON-Liste mit 3 Eintraegen."""
        patched_tag['bill_actions'].write_text(json.dumps([
            {
                'bill_id': f'hr-119-{i}',
                'title': f'Bill {i}',
                'policy_area': 'Health',
                'actions': [{'roll_call': [{'chamber': 'House', 'date': f'2025-03-{10+i:02d}T00:00:00Z'}]}],
            }
            for i in range(1, 4)
        ]), encoding='utf-8')
        patched_tag['bill_subjects'].write_text(json.dumps([
            {'bill_id': f'hr-119-{i}', 'legislative_subjects': ['Public health']}
            for i in range(1, 4)
        ]), encoding='utf-8')
        patched_tag['mapping'].write_text(
            'legislative_subject,frequency,yf_sector,yf_industry,sector_rank,konfidenz,region,kommentar\n'
            'Public health,100,Healthcare,Drug Manufacturers,1,Hoch,,\n',
            encoding='utf-8',
        )
        tag = VoteTimingTag()
        result = tag.apply(make_trade(transaction_date='2025-03-01'))
        assert result.iloc[0]['Vote_Timing_Hit_Count'] == 3
        hits_list = json.loads(result.iloc[0]['Vote_Timing_Hits'])
        assert len(hits_list) == 3

    def test_apply_hits_haben_korrekte_days_to_vote(self, patched_tag):
        """Trade 2025-03-01, Vote 2025-03-15 → days_to_vote=14."""
        write_minimal_fixtures(patched_tag, vote_date='2025-03-15T12:00:00Z')
        tag = VoteTimingTag()
        result = tag.apply(make_trade(transaction_date='2025-03-01'))
        hits = json.loads(result.iloc[0]['Vote_Timing_Hits'])
        assert hits[0]['days_to_vote'] == 14
