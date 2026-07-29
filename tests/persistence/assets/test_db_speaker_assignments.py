from stt_vault.persistence import db
from stt_vault.persistence.assets import db_speaker_assignments, db_speakers


def test_speaker_assignment_operations_have_a_dedicated_persistence_owner() -> None:
    assignment_operations = {
        "delete_speaker",
        "list_asset_ids_for_speaker",
        "merge_speakers",
        "refresh_asset_transcripts_for_speaker_from_conn",
        "relabel_asset_speaker",
        "relabel_asset_speakers",
        "rename_speaker",
    }

    for operation in assignment_operations:
        assert getattr(db, operation) is getattr(db_speaker_assignments, operation)
        assert not hasattr(db_speakers, operation)
